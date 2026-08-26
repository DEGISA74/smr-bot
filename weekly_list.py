#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HAFTALIK PARA AKIŞI LİSTESİ + AÇIK KARNE → admin Telegram (her Pazar sabahı).

factor_rank (CMF — tek backtest-kanıtlı, rejim-dayanıklı faktör) top 10 hisse +
GEÇEN haftanın gerçek sonucu (liste ortalaması vs XU100). Şeffaf sicil birikir.

Standalone, ~/smr içinden çalışır (patron.db + veriler yanında). Token weektweet/.env'den.
--test : anında gönder, weekly_list tablosuna YAZMA (sicili kirletme).
"""
import sqlite3, os, sys, requests
import numpy as np
import pandas as pd
from datetime import datetime
from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date, skip_previous_report
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, 'patron.db')
# Haftalık sicil AYRI dosyada — patron.db lokal→VPS sync'i (app.py atomik mv) haftalık
# kaydı EZMESİN. Aksi halde karne her hafta silinir, "İlk hafta" der (eski bug buydu).
HIST_DB = os.path.join(BASE, 'weekly_history.db')
VERILER = os.path.join(BASE, 'veriler')
ADMIN_ID = '1034525990'
ELITE_CHAT = '-1003769685835'   # SMR Elite kanalı — para akışı listesi buraya
TOPN = 10
MIN_TL_VOL = 250_000_000   # 20g ort günlük TL hacim tabanı — BÜYÜK-CAP (pump/mikro-cap dışarı)
MOM_MAX = 150.0            # momentum tavanı %: yükselen ama PARABOLİK değil (1400% pump'lar elenir)
TEST = '--test' in sys.argv


def _token():
    for p in ('/home/wm11tr/weektweet/.env', '/home/wm11tr/insider/.env'):
        try:
            for line in open(p):
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    return line.split('=', 1)[1].strip()
        except Exception:
            pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')


def tg_send(chat_id, text, parse_mode=None):
    tok = _token()
    if not tok:
        print('token bulunamadı'); return False
    try:
        payload = {'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True}
        if parse_mode:
            payload['parse_mode'] = parse_mode
        r = requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          json=payload, timeout=25)
        if r.status_code != 200:
            print('telegram HTTP', r.status_code, r.text[:160]); return False
        return True
    except Exception as e:
        print('telegram hata', e); return False


def _apply_split_adjustments(df):
    """İşlenmemiş BIST bölünme/bedelsiz düzeltmesi — app.py ile birebir. Tek günde >%20
    düşüş = bölünme (BIST limit ±%10). Standalone scriptler ham parquet okuduğu için şart."""
    if df is None or df.empty or len(df) < 5 or 'Close' not in df.columns:
        return df
    df = df.copy().sort_index()
    pc = [c for c in ['Open', 'High', 'Low', 'Close'] if c in df.columns]
    for _ in range(10):
        cl = df['Close'].ffill().values
        found = False
        for i in range(1, len(cl)):
            if cl[i - 1] <= 0 or cl[i] <= 0:
                continue
            ratio = cl[i - 1] / cl[i]
            if ratio >= 1.20:
                for col in pc:
                    df.iloc[:i, df.columns.get_loc(col)] = df.iloc[:i][col].values / ratio
                if 'Volume' in df.columns:
                    df.iloc[:i, df.columns.get_loc('Volume')] = df.iloc[:i]['Volume'].values * ratio
                found = True
                break
        if not found:
            break
    return df


def _liquidity_manip(df):
    """app.py BİREBİR — ince tahta + dik koşu + hacim sıçraması = pompa riski (yüksek/orta/yok)."""
    try:
        c = df['Close']; v = df['Volume']
        if len(c) < 20:
            return 'yok'
        adv = float((c * v).tail(20).mean())
        tier = 'yüksek' if adv >= 300e6 else ('orta' if adv >= 30e6 else 'düşük')
        avgv = float(v.tail(20).mean())
        rvol = float(v.iloc[-1] / avgv) if avgv > 0 else 0.0
        run5 = float(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 6 else 0.0
        run10 = float(c.iloc[-1] / c.iloc[-11] - 1) if len(c) > 11 else 0.0
        thin = tier in ('düşük', 'orta')
        steep = (run5 > 0.20) or (run10 > 0.40)
        spike = rvol > 3
        return 'yüksek' if (thin and steep and spike) else ('orta' if (thin and (steep or spike)) else 'yok')
    except Exception:
        return 'yok'


def _read_adj(sym):
    """Parquet oku + bölünme düzeltmesi uygula. df ya da None."""
    for cand in (f"{VERILER}/{sym}_1d.parquet", f"{VERILER}/{sym}.IS_1d.parquet"):
        if os.path.exists(cand):
            try:
                return _apply_split_adjustments(pd.read_parquet(cand))
            except Exception:
                return None
    return None


def _last_close(sym):
    df = _read_adj(sym)
    try:
        return float(df['Close'].dropna().iloc[-1]) if df is not None else None
    except Exception:
        return None


def _is_manip(df):
    """BRMEN tipi manipülasyon şüphesi — tavan motorundaki _tav_is_manipulated ile BİREBİR.
    (1) fitilsiz mum çok, (2) tavan/taban açılış çok (>%9.5 günlük), (3) range çöküşü
    (tavanda kilitli). En az 2 ölçüt kırmızıysa True → listeden elenir."""
    i = len(df)
    if i < 60:
        return False
    last30 = df.iloc[i - 30:i]
    rng = last30['High'] - last30['Low']
    body = (last30['Close'] - last30['Open']).abs()
    body_ratio = (body / rng.replace(0, np.nan)).fillna(1.0)
    fitilsiz = (body_ratio > 0.85).mean()                       # fitilsiz mum oranı
    last60 = df.iloc[max(0, i - 60):i]
    tavan = (last60['Close'].pct_change().abs() * 100 > 9.5).mean()   # tavan/taban açılış
    last20 = df.iloc[i - 20:i]
    rel = ((last20['High'] - last20['Low']) / last20['Close']) * 100
    collapse = (rel < 1.0).mean()                               # range çöküşü
    red = (fitilsiz > 0.45) + (tavan > 0.12) + (collapse > 0.30)
    return int(red) >= 2


def _is_extended(df):
    """Yakın-dönem dikey tavan — 'direkt tavan ile açan, orada kalan' (BRMEN tipi).
    BIST günlük limit ~%10. Son gün ≥%9 (bugün tavanda → kovalama) ya da son 5 günde
    2+ tavan günü (dikey pump) → ele. Tavanda olanı listeye koymak hem riskli hem geç."""
    ch = df['Close'].pct_change() * 100
    if ch.iloc[-1] >= 9.0:
        return True
    if int((ch.tail(5) >= 9.0).sum()) >= 2:
        return True
    return False


def _eval_candidate(sym):
    """(close, 20g TL hacim, ele_mi, mom_12_1_%) — tek parquet okuması.
    ele_mi = kronik manipülasyon VEYA yakın-dönem dikey tavan. mom = 12-1 ay momentum."""
    df = _read_adj(sym)
    if df is None:
        return None, None, None, None
    try:
        c = df['Close']
        close = float(c.dropna().iloc[-1])
        tlv = float((df['Volume'] * c).tail(20).mean())
        bad = _is_manip(df) or _is_extended(df) or _liquidity_manip(df) == 'yüksek'
        # ~11 ay momentum (son ayı atla) — VPS parquet'leri ~251 bar; 12-1 ay ile ~aynı
        mom = ((float(c.iloc[-21]) / float(c.iloc[-231]) - 1) * 100) if len(df) >= 235 else None
        return close, tlv, bad, mom
    except Exception:
        return None, None, None, None


def select_leaders(conn, topn=TOPN):
    """Filtre zinciri: CMF-top → likidite ≥tabanı → manipülasyon değil → tavan değil →
    momentum>0. backtest-kanıtlı combo. Liste döner: [{symbol,cmf_pct,mom,close}]."""
    fr = pd.read_sql("SELECT symbol,cmf_pct FROM factor_rank "
                     "WHERE rank_date=(SELECT MAX(rank_date) FROM factor_rank) "
                     "ORDER BY cmf_pct DESC LIMIT 250", conn)
    picks = []
    for r in fr.itertuples():
        ep, tlv, bad, mom = _eval_candidate(r.symbol)
        if (ep and tlv and tlv >= MIN_TL_VOL and not bad
                and mom is not None and 0 < mom <= MOM_MAX):
            picks.append({'symbol': r.symbol, 'cmf_pct': float(r.cmf_pct), 'mom': mom, 'close': ep})
        if len(picks) >= topn:
            break
    return picks


def write_flow_leaders(conn, picks, rdate):
    """app.py render'ının okuyacağı flow_leaders tablosu (en güncel liste)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS flow_leaders(
        snap_date TEXT, rank INTEGER, symbol TEXT, cmf_pct REAL, mom_pct REAL, close REAL,
        PRIMARY KEY(snap_date,rank))""")
    conn.execute("DELETE FROM flow_leaders WHERE snap_date=?", (rdate,))
    conn.executemany("INSERT INTO flow_leaders(snap_date,rank,symbol,cmf_pct,mom_pct,close) VALUES(?,?,?,?,?,?)",
                     [(rdate, i + 1, p['symbol'], p['cmf_pct'], p['mom'], p['close']) for i, p in enumerate(picks)])
    conn.commit()


def scorecard_rows(conn, today):
    """Bugünden ÖNCEKİ en son haftalık listenin per-hisse sonucu (kapanış + zirve getirisi).
    Zirve = giriş tarihinden SONRAKİ barların en yükseği. Kapanışa göre sıralı döner:
    {'week','rows':[{symbol,close_ret,peak_ret}],'avg','avg_peak','n','n_pos','xu'} ya da None."""
    prior = conn.execute("SELECT DISTINCT week_date FROM weekly_list WHERE week_date < ? "
                         "ORDER BY week_date DESC LIMIT 1", (today,)).fetchone()
    if not prior:
        return None
    wd = prior[0]
    recs = conn.execute("SELECT symbol,entry_price,entry_date FROM weekly_list "
                        "WHERE week_date=? AND symbol!='_XU100_'", (wd,)).fetchall()
    rows = []
    for sym, ep, edate in recs:
        if not ep or ep <= 0:
            continue
        df = _read_adj(sym)
        if df is None or df.empty or 'Close' not in df.columns:
            continue
        try:
            lc = float(df['Close'].dropna().iloc[-1])
        except Exception:
            continue
        peak = None
        try:
            after = df[df.index > pd.Timestamp(edate)] if edate else df.tail(6)
            if not after.empty and 'High' in after.columns:
                pv = float(after['High'].max())
                if pv > 0:
                    peak = pv
        except Exception:
            peak = None
        close_ret = (lc / ep - 1) * 100
        peak_ret = ((peak / ep - 1) * 100) if peak else close_ret
        rows.append({'symbol': sym.replace('.IS', ''), 'close_ret': close_ret, 'peak_ret': peak_ret})
    if not rows:
        return None
    rows.sort(key=lambda r: r['close_ret'], reverse=True)   # kapanışa göre sırala
    n = len(rows)
    out = {'week': wd, 'rows': rows,
           'avg': sum(r['close_ret'] for r in rows) / n,
           'avg_peak': sum(r['peak_ret'] for r in rows) / n,
           'n': n, 'n_pos': sum(1 for r in rows if r['close_ret'] > 0), 'xu': None}
    xu = conn.execute("SELECT entry_price FROM weekly_list WHERE week_date=? AND symbol='_XU100_'", (wd,)).fetchone()
    if xu and xu[0]:
        xc = _last_close('XU100.IS')
        if xc:
            out['xu'] = (xc / xu[0] - 1) * 100
    return out


_MEDALS = {1: '🥇', 2: '🥈', 3: '🥉'}


def build_recap_msg(sc):
    """GEÇEN HAFTANIN LİSTESİ — NE YAPTI? (günlük yüksek-getiri formatı).
    Kapanış getirisi KALIN (birincil), zirve getirisi İTALİK (soluk/ikincil)."""
    wd = datetime.strptime(sc['week'], '%Y-%m-%d').strftime('%d.%m')
    bugun = datetime.now().strftime('%d.%m')
    L = ["📋 <b>GEÇEN HAFTANIN LİSTESİ — NE YAPTI?</b>",
         f"{wd} → {bugun} · geçen hafta girişine göre", ""]
    for i, r in enumerate(sc['rows'], 1):
        pre = _MEDALS.get(i, f"{i:>2}")
        sym = r['symbol'][:6].ljust(6)
        close_s = f"%{r['close_ret']:+.1f}"
        peak_s = f"zirve %{r['peak_ret']:+.0f}"
        L.append(f"{pre} {sym} <b>{close_s:>7}</b>   <i>{peak_s}</i>")
    L.append("")
    xu_part = (f" · XU100 <b>%{sc['xu']:+.1f}</b>" if sc['xu'] is not None else "")
    L.append(f"🏁 Ortalama <b>%{sc['avg']:+.1f}</b> (kapanış) · <i>zirve ort. %{sc['avg_peak']:+.0f}</i>{xu_part}")
    L.append(f"{sc['n']} hisseden {sc['n_pos']}'si haftayı artıda kapadı.")
    L.append("ℹ️ Eğitim amaçlıdır, yatırım tavsiyesi değildir.")
    return "\n".join(L)


def main():
    conn = sqlite3.connect(DB)
    today = datetime.now().strftime('%Y-%m-%d')
    if not conn.execute("SELECT COUNT(*) FROM factor_rank").fetchone()[0]:
        conn.close()
        raise DataUnavailable('factor_rank boş; universe_snapshot güncel para akışı verisi üretmedi.')
    rdate = conn.execute("SELECT MAX(rank_date) FROM factor_rank").fetchone()[0]
    require_data_date(rdate, timing='weekly', enabled='--test' not in sys.argv)
    # Haftalık sicil AYRI dosyada (HIST_DB) — patron.db sync ezmesin diye.
    hconn = sqlite3.connect(HIST_DB)
    hconn.execute("""CREATE TABLE IF NOT EXISTS weekly_list(
        week_date TEXT, symbol TEXT, cmf_pct REAL, entry_price REAL, entry_date TEXT,
        PRIMARY KEY(week_date,symbol))""")
    sc = None if skip_previous_report() else scorecard_rows(hconn, today)  # eski haftayı yeni karne sanma
    picks = select_leaders(conn)            # CMF + likidite + manipülasyon-değil + tavan-değil + momentum>0
    if not picks:
        print('liste boş (filtreler hepsini eledi)'); conn.close(); hconn.close(); return

    # ── Mesaj 2: bu haftanın yeni listesi (düz metin, eskisiyle aynı) ──
    L = [f"📊 HAFTALIK PARA AKIŞI LİSTESİ — {datetime.now():%d.%m.%Y}",
         "Para akışı (CMF) + yükselen trend (momentum) — 14 ay backtest + rejim testinden geçen combo."]
    if not sc:
        L.append("\n(İlk hafta — açık karne gelecek hafta başlar.)")
    L.append("\nBu hafta (akış + trend, likit & filtreli):")
    for i, p in enumerate(picks, 1):
        L.append(f"{i:>2}. {p['symbol'].replace('.IS',''):<7} — akış {p['cmf_pct']:.0f}/100 · trend +%{p['mom']:.0f}")
    L.append("\nℹ️ Eğitim amaçlıdır, yatırım tavsiyesi değildir.")
    list_msg = "\n".join(L)

    _target = ADMIN_ID if TEST else ELITE_CHAT   # test → kendine; gerçek → SMR Elite kanalı

    # ── Mesaj 1: geçen haftanın karnesi (varsa ÖNCE gönderilir — günlük formatı) ──
    if sc:
        recap = build_recap_msg(sc)
        print(recap); print("---")
        tg_send(_target, recap, parse_mode='HTML')   # best-effort; başarısızsa liste yine gider

    print(list_msg)
    if not tg_send(_target, list_msg):
        print('GÖNDERİM BAŞARISIZ — state yazılmadı'); conn.close(); hconn.close(); return
    print(f'✅ Telegram gönderildi ({"ADMIN test" if TEST else "SMR Elite"})')
    if not TEST:
        write_flow_leaders(conn, picks, rdate)
        store = [(today, p['symbol'], p['cmf_pct'], p['close'], rdate) for p in picks]
        store.append((today, '_XU100_', None, _last_close('XU100.IS'), rdate))
        hconn.executemany("INSERT OR REPLACE INTO weekly_list(week_date,symbol,cmf_pct,entry_price,entry_date) "
                          "VALUES(?,?,?,?,?)", store)
        hconn.commit()
        print(f'✅ weekly_list (sicil) + flow_leaders kaydedildi ({len(picks)} hisse, hafta {today})')
    conn.close(); hconn.close()


def leaders_only():
    """Sadece flow_leaders'ı güncelle (app paneli için, Telegram YOK) — hafta içi nightly."""
    conn = sqlite3.connect(DB)
    if not conn.execute("SELECT COUNT(*) FROM factor_rank").fetchone()[0]:
        print('factor_rank boş'); conn.close(); return
    rdate = conn.execute("SELECT MAX(rank_date) FROM factor_rank").fetchone()[0]
    picks = select_leaders(conn)
    write_flow_leaders(conn, picks, rdate)
    print(f'✅ flow_leaders güncellendi: {len(picks)} hisse · tarih {rdate}')
    conn.close()


if __name__ == '__main__':
    if '--leaders' in sys.argv:
        leaders_only()
    else:
        raise SystemExit(guarded_main(
            engine='weekly_para_akisi',
            label='HAFTALIK PARA AKIŞI MOTORU',
            main_func=main,
            targets=(ADMIN_ID if TEST else ELITE_CHAT,),
            base=BASE,
            live=not TEST,
            cadence='weekly',
        ))
