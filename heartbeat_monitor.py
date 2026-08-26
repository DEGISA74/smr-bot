#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HEARTBEAT MONITOR — "denetimleri denetleyen bekçi" (19 Haz 2026).
Ölçüm/koruma sistemi sessizce ölürse hiçbir işe yaramaz → bu bekçi onu izler.

ÖLÜ-ADAM ANAHTARI: HER GÜN Telegram'a mesaj atar (yeşil=sağlıklı / kırmızı=sorun).
  → Mesaj GELMEZSE bekçinin KENDİSİ ölmüş demektir (nihai denetim).

Denetlediği şeyler:
  1. Loglama canlı mı (scan_signals taze mi — Master Scan durdu mu)
  2. Getiri pipeline (signal_results büyüyor mu — backtest motoru)
  3. VPS sync canlı mı (bot kanıt köprüsü taze mi — sessizce ölmüş mü)
  4. Servisler aktif mi (smr-bot + patron-radar)
  5. Öz-denetim araçları yerinde mi (scanner_karne)
  6. Hızlı flag anomalisi (yeni/bilinmeyen kırık)

Çalıştır: python heartbeat_monitor.py   (Windows Task Scheduler ile günde 1)
"""
import sqlite3, json, os, subprocess, datetime as dt, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(BASE, 'patron.db')
VPS  = 'wm11tr@34.153.19.220'
ADMIN = 1034525990
today = dt.date.today()

try:
    CFG = json.load(open(os.path.join(BASE, 'telegram_config.json'), encoding='utf-8'))
    TOKEN = CFG['bot_token']
except Exception as e:
    print("telegram_config.json okunamadı:", e); TOKEN = None


def tg(msg):
    if not TOKEN:
        print("(token yok — gönderilemedi)\n", msg); return
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({'chat_id': ADMIN, 'text': msg, 'parse_mode': 'Markdown'}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=25)
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def ssh(cmd, timeout=30):
    return subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', VPS, cmd],
                          capture_output=True, text=True, timeout=timeout)


problems = []; info = []

# 1) Loglama canlı mı + 2) getiri pipeline
try:
    c = sqlite3.connect(DB); cur = c.cursor()
    last = cur.execute("SELECT MAX(scan_date) FROM scan_signals").fetchone()[0]
    if last:
        gun = (today - dt.date.fromisoformat(last)).days
        info.append(f"Son tarama {last} ({gun}g)")
        if gun > 7:
            problems.append(f"🔴 LOGLAMA DURMUŞ — {gun} gündür Master Scan yok (bot kanıtı + karne ölüyor)")
    else:
        problems.append("🔴 scan_signals BOŞ — hiç tarama yok")
    r5 = cur.execute("SELECT COUNT(*) FROM signal_results WHERE ret_5g IS NOT NULL").fetchone()[0]
    info.append(f"Getiri kaydı {r5}")
    # 6) hızlı flag anomalisi (son 14g, çekirdek feature boş mu)
    cutoff = (today - dt.timedelta(days=14)).isoformat()
    ntot = cur.execute("SELECT COUNT(*) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
    if ntot >= 30:
        for col in ['f_rsi', 'f_52h_pos', 'f_master_score']:   # çekirdek — daima dolmalı
            nf = cur.execute(f"SELECT COUNT({col}) FROM scan_signals WHERE scan_date>=?", (cutoff,)).fetchone()[0]
            if nf / ntot < 0.5:
                problems.append(f"🔴 ÇEKİRDEK FLAG BOZUK — {col} sadece %{nf/ntot*100:.0f} dolu")
    c.close()
except Exception as e:
    problems.append(f"🔴 LOKAL DB okunamadı: {str(e)[:70]}")

# 3) VPS sync canlı mı (bot kanıt köprüsü)
try:
    out = ssh("cd ~/smr && python3 -c \"import sqlite3;print(sqlite3.connect('patron.db')"
              ".execute('SELECT MAX(scan_date) FROM scan_signals').fetchone()[0])\"")
    vlast = out.stdout.strip()
    if vlast and vlast not in ('None', ''):
        vgun = (today - dt.date.fromisoformat(vlast)).days
        info.append(f"VPS DB {vlast} ({vgun}g)")
        if vgun > 7:
            problems.append(f"🔴 SYNC PATLAMIŞ / VPS BAYAT — bot kanıt köprüsü {vgun}g (sessizce öldü)")
    else:
        problems.append("🔴 VPS patron.db okunamadı (sync/bot şüpheli)")
except Exception as e:
    problems.append(f"🔴 VPS erişilemedi — sync/bot kontrol EDİLEMEDİ: {str(e)[:50]}")

# 4) Servisler aktif mi
try:
    out = ssh("systemctl is-active smr-bot patron-radar", timeout=20)
    st = out.stdout.split()
    if st.count('active') < 2:
        problems.append(f"🔴 SERVİS SORUNU — smr-bot/patron-radar: {' '.join(st) or 'yanıt yok'}")
    else:
        info.append("Servisler aktif")
except Exception as e:
    problems.append(f"🔴 Servis durumu alınamadı: {str(e)[:50]}")

# 5) Öz-denetim araçları yerinde mi
if not os.path.exists(os.path.join(BASE, 'scanner_karne.py')):
    problems.append("🔴 scanner_karne.py KAYIP — öz-denetim aracı yok!")

# 6) KONTROLLER GERÇEKTEN YAPILDI MI — heartbeat kontrolü kendisi çalıştırır
controls_line = ""
try:
    import scanner_karne
    cc = scanner_karne.controls_check()
    if not cc['ran']:
        controls_line = "⚠️ KONTROLLER YAPILAMADI (yetersiz veri / scan_signals boş)"
        problems.append(controls_line)
    elif cc['new_anomalies']:
        controls_line = f"🔴 KONTROL: {len(cc['new_anomalies'])} YENİ anomali → " + " · ".join(cc['new_anomalies'][:4])
        problems.append(controls_line)
    elif not cc['data_fresh']:
        controls_line = "⚠️ KONTROL yapıldı ama VERİ BAYAT (7g+) — taze sinyal yok"
        problems.append(controls_line)
    else:
        controls_line = f"Kontroller yapıldı ✓ temiz ({cc['known_n']} bilinen, yeni anomali yok)"
        info.append(controls_line)
except Exception as e:
    controls_line = f"⚠️ KONTROL ÇALIŞTIRILAMADI: {str(e)[:60]}"
    problems.append(controls_line)

# 7) GİRDİ-VERİ DOĞRULUĞU (en temel katman — çöp girer çöp çıkar)
try:
    import scanner_karne as _sk
    di = _sk.data_integrity_check()
    if di['problems']:
        for _p in di['problems']:
            problems.append(f"🔴 VERİ: {_p}")
    elif di['ran']:
        info.append("Girdi-veri doğru ✓ (XU100+fiyat+eşik)")
    else:
        problems.append("⚠️ Veri denetimi yapılamadı")
except Exception as e:
    problems.append(f"⚠️ VERİ DENETİMİ ÇALIŞMADI: {str(e)[:50]}")

# 8) İZLEMEDEKİ ADAY FİLTRELER — cross-rejim gelince "karar zamanı" diye uyar
try:
    import scanner_karne as _sk2
    _ow = _sk2.observation_watch()
    if any('KARAR ZAMANI' in _l for _l in _ow):
        problems.append("🟡 İZLEME: VIP RSI 55-70 için CROSS-REJİM VERİ GELDİ → doğrula + karar ver (bkz karne)")
    else:
        info.append("İzlemede: VIP RSI55-70 (tek-rejim, Temmuz bekliyor)")
except Exception:
    pass

# 9) GÜÇ-FİLTRE SÜREKLİ DENETİMİ — kullanılan bir filtre ters dönerse (drift) ALARM
try:
    import scanner_karne as _sk3
    _ga = _sk3.guc_filter_audit()
    if _ga.get('alerts'):
        for _al in _ga['alerts']:
            problems.append(f"🔴 GÜÇ DRIFT: {_al}")
    else:
        info.append("Güç filtreleri denetlendi ✓ (Harmonik dürüst)")
except Exception:
    pass

# ── MESAJ (ölü-adam anahtarı: her run atar) ──
_now = dt.datetime.now()
_saat = "🌙 Akşam" if _now.hour >= 14 else "🌅 Sabah"
hdr = f"🫀 *SMR HEARTBEAT* ({_saat}) — {_now:%d.%m.%Y %H:%M}\n"
if problems:
    msg = hdr + f"\n🚨 *{len(problems)} SORUN / EKSİK:*\n" + "\n".join(problems) + "\n\n_durum: " + " · ".join(info) + "_"
else:
    msg = (hdr + "✅ *Sistem sağlıklı + kontroller yapıldı.* Her şey ölçülüyor, akıyor, taze, denetlendi.\n"
           + " · ".join(info)
           + "\n\n_Bu mesajı her akşam (20:30) + sabah (09:30) almalısın. Gelmezse → bekçi ölmüş, elle bak._")
tg(msg)
print(msg)
