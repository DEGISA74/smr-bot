#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Infografik PNG -> SMR Elite (sendPhoto/sendDocument).
   infografik_build.render(ticker) cagrilir (kod kopyasi YOK), cikan PNG Telegram'a gider.
   Kullanim: python infografik_telegram.py TICKER [--test] [--no-render] [--doc]
     --test      ADMIN'e gonder (Elite yerine)
     --no-render  mevcut _ig_TICKER.png'yi kullan (yeniden uretme)
     --doc        foto yerine dosya olarak gonder (tam cozunurluk)
"""
import os, sys, requests
import infografik_build as ib

BASE = os.path.dirname(os.path.abspath(__file__))
ELITE_CHAT = '-1003711632362'   # SMR Elite
ADMIN_ID = '1034525990'
TEST = '--test' in sys.argv
NO_RENDER = '--no-render' in sys.argv
AS_DOC = '--doc' in sys.argv


def _token():
    for p in ('/home/wm11tr/weektweet/.env', '/home/wm11tr/insider/.env'):
        try:
            for line in open(p):
                if line.startswith('TELEGRAM_BOT_TOKEN='):
                    return line.split('=', 1)[1].strip()
        except Exception:
            pass
    return os.environ.get('TELEGRAM_BOT_TOKEN')


def tg_send_image(chat_id, path, caption='', as_doc=False):
    tok = _token()
    if not tok:
        print('token yok'); return False
    method = 'sendDocument' if as_doc else 'sendPhoto'
    field = 'document' if as_doc else 'photo'
    try:
        with open(path, 'rb') as fh:
            r = requests.post(f'https://api.telegram.org/bot{tok}/{method}',
                              data={'chat_id': chat_id, 'caption': caption},
                              files={field: fh}, timeout=120)
        if r.status_code != 200:
            print('telegram HTTP', r.status_code, r.text[:200]); return False
        return True
    except Exception as e:
        print('telegram hata', e); return False


def tg_send_text(chat_id, text):
    tok = _token()
    if not tok:
        print('token yok'); return False
    try:
        r = requests.post(f'https://api.telegram.org/bot{tok}/sendMessage',
                          json={'chat_id': chat_id, 'text': text, 'disable_web_page_preview': True},
                          timeout=30)
        if r.status_code != 200:
            print('telegram HTTP', r.status_code, r.text[:200]); return False
        return True
    except Exception as e:
        print('telegram hata', e); return False


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    tk = (args[0] if args else 'XU100').upper()
    out = os.path.join(BASE, f'_ig_{tk}.png')
    if NO_RENDER:
        if not os.path.exists(out):
            print('PNG yok, --no-render iptal'); return
    else:
        out = ib.render(tk, out=out)
        if not out:
            print('render basarisiz'); return
    target = ADMIN_ID if TEST else ELITE_CHAT
    # 1) Resim (foto)
    ok1 = tg_send_image(target, out, caption='', as_doc=AS_DOC)
    # 2) Ardından ayrı yazi mesaji
    txt = ("SMR-ELITE aboneleri için Detaylı Özel Analizdir. "
           "Eğitim amaçlıdır. Yatırım tavsiyesi değildir.\n"
           f"#SmartMoneyRadar #{tk}")
    ok2 = tg_send_text(target, txt)
    print('gonderim -> ', 'ADMIN(test)' if TEST else 'SMR Elite',
          '| foto:', 'OK' if ok1 else 'BASARISIZ', '| yazi:', 'OK' if ok2 else 'BASARISIZ')


if __name__ == '__main__':
    main()
