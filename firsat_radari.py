#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FIRSAT RADARI — canli tarama + Telegram alarm (v1).
Likit (>=500M TL/gun) tahtalarda formasyon (fincan/TOBO/cift-dip) arar, 3 kutuya ayirir:
  HAZIRLANIYOR (form, boyna >%6) / SIKISTI (form, boyna<=%6 + sikisma) / KIRDI (break).
Sadece YENI (dunku state'te olmayan) KIRDI(Q>=3) + SIKISTI alarmlarini yollar.
  --test  : ADMIN'e gonder. Argumansiz: RADAR_CHAT'e.
"""
import pandas as pd, numpy as np, glob, os, io, sys, json, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import radar_core as rc
from tarama_sureklilik import DataUnavailable, guarded_main, require_data_date

BASE = os.path.dirname(os.path.abspath(__file__))
VER = os.path.join(BASE, 'veriler')
ENDEKS = {'XU100', 'XU030', 'XU050', 'XBANK', 'XUSIN', 'XUMAL', 'XU100D', 'XGIDA', 'XBANA'}
LIK_TABAN = 500_000_000
KIRDI_MIN_Q = 3                  # KIRDI alarmi icin en az kac isaret
ADMIN_ID = '1034525990'
RADAR_CHAT = ADMIN_ID            # kanal secilince degistir (su an ADMIN'e gider)
STATE_FILE = os.path.join(BASE, 'firsat_state.json')
LOG_FILE = os.path.join(BASE, 'firsat_radar_log.jsonl')   # sürekli backtest için her gün TÜM sinyaller
TEST = '--test' in sys.argv
TYPE_TR = {'CUP': 'Fincan-Kulp', 'TOBO': 'TOBO', 'UCGEN': 'Yukselen Ucgen', 'TABAN': 'Taban', 'W': 'Cift Dip'}


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


def scan():
    return rc.scan_universe(VER, LIK_TABAN)


def _load_state():
    try:
        with open(STATE_FILE, encoding='utf-8') as fh:
            s = json.load(fh)
            for k in ('taze', 'retest', 'sikis'):
                s.setdefault(k, [])
            return s
    except Exception:
        return {'taze': [], 'retest': [], 'sikis': []}


def _save_state(kirdi, sikis):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as fh:
            json.dump({'taze': [r['tk'] for r in kirdi if r.get('durum') == 'taze'],
                       'retest': [r['tk'] for r in kirdi if r.get('durum') == 'retest'],
                       'sikis': [r['tk'] for r in sikis]}, fh, ensure_ascii=False)
    except Exception as e:
        print('state hata', e)


def _log_signals(hazir, sikis, kirdi, td):
    """Sürekli backtest için: o günün TÜM sinyallerini kalıcı log'a yazar (idempotent — gün tekrar yazılmaz)."""
    if td is None:
        return
    ds = td.strftime('%Y-%m-%d')
    try:
        with open(LOG_FILE, encoding='utf-8') as fh:
            if any(f'"{ds}"' in line and '"date"' in line for line in fh):
                return   # bu gün zaten loglandı
    except FileNotFoundError:
        pass
    n = 0
    with open(LOG_FILE, 'a', encoding='utf-8') as fh:
        for kutu, lst in (('hazir', hazir), ('sikis', sikis), ('kirdi', kirdi)):
            for r in lst:
                fh.write(json.dumps({'date': ds, 'tk': r['tk'], 'kutu': kutu, 'durum': r.get('durum', ''),
                                     'type': r['type'], 'neck': r['neck'], 'fiyat': r['fiyat'],
                                     'dist': r['dist'], 'Q': r.get('Q', 0), 'sqd': r.get('sqd', 0),
                                     'pos52': r.get('pos52', 0), 'a200': r.get('a200', 0)}, ensure_ascii=False) + '\n')
                n += 1
    print(f'log: {n} sinyal kaydedildi ({ds})')


def main():
    hazir, sikis, kirdi, td = scan()
    if td is None:
        raise DataUnavailable('Fırsat Radarı güncel seans tarihini üretemedi; parquet verisi yok veya güncel değil.')
    require_data_date(td, timing='same_day', enabled='--test' not in sys.argv)
    prev = _load_state()
    # 🚀 TAZE KIRDI: yeni kirdi, boynun ustunde, uzamamis, Q>=3
    taze = [r for r in kirdi if r.get('durum') == 'taze' and r['Q'] >= KIRDI_MIN_Q
            and r['fiyat'] >= r['neck'] and r.get('ext', 1.0) <= 1.20 and r['tk'] not in prev['taze']]
    # 🔄 GERI TEST: kirdiktan sonra boyna dondu, trend yukari (kurumsal giris bolgesi)
    retest = [r for r in kirdi if r.get('durum') == 'retest' and r['a200'] == 1 and r['tk'] not in prev['retest']]
    # 🤏 TAKIBE AL: kirilma yakin
    s_alarm = [r for r in sikis if r['tk'] not in prev['sikis']]
    ds = td.strftime('%d.%m.%Y') if td is not None else ''
    L = [f"🎯 FIRSAT RADARI — {ds}"]
    if taze:
        L.append("\n🚀 BOYUN KIRDI (taze — yeni):")
        for r in taze:
            L.append(f"• {r['tk']} ({TYPE_TR.get(r['type'], r['type'])}) — {r['Q']}/5 işaret · boyun {r['neck']}")
    if retest:
        L.append("\n🔄 GERİ TEST (boyna döndü — tutarsa kurumsal giriş — yeni):")
        for r in retest:
            L.append(f"• {r['tk']} ({TYPE_TR.get(r['type'], r['type'])}) — fiyat {r['fiyat']} · destek {r['neck']}")
    if s_alarm:
        L.append("\n🤏 TAKİBE AL (kırılma yakın — yeni):")
        for r in s_alarm:
            L.append(f"• {r['tk']} ({TYPE_TR.get(r['type'], r['type'])}) — boyna %{r['dist']} · sıkışmış")
    if not (taze or retest or s_alarm):
        print(f'Yeni alarm yok (KIRDI {len(kirdi)} / SIKISTI {len(sikis)} / HAZIR {len(hazir)})')
    else:
        L.append("\nℹ️ Algoritmanın izlediği formasyon kırılımları — kesinlik değil, olasılık. Yatırım tavsiyesi değildir.")
        msg = "\n".join(L)
        print(msg)
        target = ADMIN_ID if TEST else RADAR_CHAT
        print('gonderim:', 'OK' if tg_send(target, msg) else 'BASARISIZ', '->', 'ADMIN(test)' if TEST else RADAR_CHAT)
    if not TEST:
        _save_state(kirdi, sikis)
        _log_signals(hazir, sikis, kirdi, td)   # sürekli backtest için günlük kayıt


if __name__ == '__main__':
    raise SystemExit(guarded_main(
        engine='firsat_radari',
        label='FIRSAT RADARI',
        main_func=main,
        targets=(ADMIN_ID if TEST else RADAR_CHAT,),
        base=BASE,
        live=not TEST,
    ))
