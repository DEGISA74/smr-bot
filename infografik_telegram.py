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
    text = text.replace(" — ", ", ").replace(" – ", ", ").replace("—", "-").replace("–", "-")  # AI em-dash temizle
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


def veri_tarihi(tk):
    """Depodaki (fetcher'ın onaylı parquet'i) son barın tarihi; okunamazsa None."""
    import pandas as pd
    try:
        df = pd.read_parquet(os.path.join(BASE, 'veriler', f'{tk}.IS_1d.parquet'), columns=['Close'])
        df = df.dropna(subset=['Close'])
        return pd.Timestamp(df.index[-1]).date() if len(df) else None
    except Exception:
        return None


def veri_bugune_ait_mi(tk, bugun=None):
    """2 Eki 2026 — akşam görseli YALNIZ bugünün kapanışıyla gider (tatil sonrası ilk
    işlem gününde de). Tam tatilde cron kapısı (islem_gunu_mu.py) zaten başlatmaz."""
    from datetime import datetime, timedelta
    bugun = bugun or (datetime.utcnow() + timedelta(hours=3)).date()
    son = veri_tarihi(tk)
    return son == bugun, son, bugun


# ── KESİN KAPANIŞ KAPISI (2 Eki 2026) ─────────────────────────────────────────
# Cron 19:05 TR = fetcher kapanis_final turunun BAŞLADIĞI dakika; görsel eskiden turu
# beklemeden çiziliyordu. Artık: (1) bugünkü kesin kapanış turunun TAMAMLANMASI ve
# onaylı sürüme terfisi beklenir, (2) görsele girecek fiyat onaylı kapanışla kıyaslanır.
# Hepsi yerel dosya okur (fetcher log/geçmiş + bist_data_store) — Yahoo isteği YOK.
KAPANIS_SON_BEKLEME_TR = (19, 50)    # bu saate kadar tur bitmezse Elite'e gönderilmez


def _simdi_utc():
    from datetime import datetime
    return datetime.utcnow()


def kesin_kapanis_turu(gun_utc):
    """Bugünkü kesin kapanış turu bitti mi? (başlangıç_ts, bitiş_kaydı) — bitmediyse bitiş None.
    Başlangıç: logs/fetcher.log '=== KAPANIS FINAL'; bitiş: logs/fetcher_history.jsonl'de o
    andan sonraki, onaylı sürüme terfi etmiş (promotion_ok) yfinance tur kaydı."""
    import json
    gun = gun_utc.isoformat()
    bas = None
    try:
        with open(os.path.join(BASE, 'logs', 'fetcher.log'), encoding='utf-8', errors='ignore') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 3_000_000))
            for line in f:
                if line.startswith(gun) and '=== KAPANIS FINAL' in line:
                    bas = line[:19].replace(' ', 'T')
    except OSError:
        return None, None
    if not bas:
        return None, None
    try:
        with open(os.path.join(BASE, 'logs', 'fetcher_history.jsonl'), encoding='utf-8') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 2_000_000))
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if (str(d.get('ts', '')) >= bas and d.get('source') == 'yfinance'
                        and d.get('promotion_ok')):
                    return bas, d
    except OSError:
        pass
    return bas, None


def onayli_kapanis(tk):
    """Aktif onaylı sürümdeki (bist_data_store) son bar: (tarih, kapanış)."""
    import pandas as pd
    from bist_data_store import read_active
    df = read_active(f'{tk}.IS')
    if df is None or not len(df):
        return None, None
    df = df.dropna(subset=['Close'])
    return pd.Timestamp(df.index[-1]).date(), float(df['Close'].iloc[-1])


def gorsel_fiyati(tk):
    """Görsele yazılacak fiyat — infografik_build ile AYNI yol (ig.load → ig.compute)."""
    import pandas as pd
    df = ib.ig.load(tk)
    if df is None or not len(df):
        return None, None
    d = ib.ig.compute(tk, df)
    return pd.Timestamp(df.index[-1]).date(), float(d['last'])


def kapanis_hazir_mi(tk, bekle=True):
    """(hazır_mı, açıklama). bekle=True → tur bitene dek 30 sn'de bir bakar (son 19:50 TR)."""
    import time
    from datetime import timedelta
    while True:
        simdi = _simdi_utc()
        bugun = (simdi + timedelta(hours=3)).date()
        bas, bitis = kesin_kapanis_turu(simdi.date())
        if bitis is not None:
            break
        son = (simdi + timedelta(hours=3)).replace(hour=KAPANIS_SON_BEKLEME_TR[0],
                                                   minute=KAPANIS_SON_BEKLEME_TR[1], second=0)
        if not bekle or simdi + timedelta(hours=3) >= son:
            return False, ("kesin kapanış turu " + ("başladı ama bitmedi" if bas else "bugün başlamadı")
                           + f" ({son.strftime('%H:%M')} TR'ye kadar beklendi)")
        time.sleep(30)
    o_tarih, o_kap = onayli_kapanis(tk)
    g_tarih, g_fiyat = gorsel_fiyati(tk)
    if o_tarih != bugun:
        return False, f"onaylı kapanışın tarihi {o_tarih}, beklenen bugün {bugun}"
    if g_tarih != bugun:
        return False, f"görselin verisi {g_tarih} tarihli, beklenen bugün {bugun}"
    if g_fiyat is None:
        return False, "görsel fiyatı okunamadı"
    if abs(g_fiyat - o_kap) > 0.005:
        return False, f"görseldeki fiyat {g_fiyat:.2f} ≠ onaylı kapanış {o_kap:.2f}"
    return True, (f"kesin kapanış turu {bitis.get('ts')} UTC bitti · onaylı kapanış {o_kap:.2f} "
                  f"= görsel fiyatı {g_fiyat:.2f} · sürüm {bitis.get('version_id')}")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    tk = (args[0] if args else 'XU100').upper()
    out = os.path.join(BASE, f'_ig_{tk}.png')
    if '--tarih-kontrolsuz' not in sys.argv:
        ok, son, bugun = veri_bugune_ait_mi(tk)
        if not ok:
            msg = (f"⚠️ İnfografik ({tk}) GÖNDERİLMEDİ: görselin dayanacağı son veri "
                   f"{son.strftime('%d.%m.%Y') if son else 'okunamadı'}, beklenen bugün "
                   f"{bugun.strftime('%d.%m.%Y')}. Kapanış verisi gelince elle: "
                   f"python infografik_telegram.py {tk}")
            print(msg)
            tg_send_text(ADMIN_ID, msg)
            return
        hazir, aciklama = kapanis_hazir_mi(tk)
        print('[kapanis-kapisi]', aciklama)
        if not hazir:
            msg = (f"⚠️ İnfografik ({tk}) GÖNDERİLMEDİ — kapanış hazır değil: {aciklama}. "
                   f"Kesin kapanış oturunca elle: python infografik_telegram.py {tk}")
            tg_send_text(ADMIN_ID, msg)
            return
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
