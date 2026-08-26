#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RAM BEKÇİSİ — VPS boğulmadan (swap thrash → SSH kilidi → Reset zorunlu) ÖNCE müdahale eder.

958MB'lik kutuda 3 ağır süreç (patron-radar + free-showcase + smr-bot) + taramalar
belleği doldurunca kutu swap'i dövmeye başlıyor; Python uygulamaları ve SSH kilitleniyor
(28 Tem 2026 öğlen olayı + 2 gün önceki tam donma = aynı kök: RAM darlığı).

Boş RAM kritik eşiğin altına 2 TUR ÜST ÜSTE inerse (anlık Master Scan sıçraması değil,
sürekli baskı), en çok bellek yiyen ağır Streamlit'i (patron-radar / free-showcase)
yeniden başlatır → belleği boşaltır → kutu donmadan kurtulur. smr-bot'a DOKUNMAZ
(alarm kanalı + çekirdek). Aksiyon sonrası 15 dk susar (flap önleme).

systemd ram-bekcisi.timer ile 5 dk'da bir, ROOT olarak çalışır (systemctl restart yetkisi).
"""
import os
import json
import subprocess
import time
import urllib.request
import urllib.parse

THRESHOLD_MB = 130         # boş RAM bunun altında = tehlike bölgesi (idle ~264MB, wedge ~138MB)
NEED_STREAK = 2            # kaç ardışık düşük tur → aksiyon (kısa sıçramayı yut)
COOLDOWN_S = 900           # aksiyondan sonra 15 dk sus
SVCS = ["patron-radar", "free-showcase"]   # adaylar — smr-bot HARİÇ (alarm/çekirdek)
STATE = "/home/wm11tr/smr/health/ram_bekci_state.json"
LOG = "/home/wm11tr/smr/logs/ram_bekcisi.log"
CFG = "/home/wm11tr/smr/telegram_config.json"
ADMIN = 1034525990


def _avail_mb():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemAvailable:"):
                    return int(ln.split()[1]) // 1024
    except Exception:
        pass
    return 9999


def _mem_bytes(svc):
    try:
        out = subprocess.run(["systemctl", "show", svc, "-p", "MemoryCurrent", "--value"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return int(out) if out.isdigit() else -1
    except Exception:
        return -1


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _tg(text):
    try:
        tok = json.load(open(CFG))["bot_token"]
        data = urllib.parse.urlencode({"chat_id": ADMIN, "text": text}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok,
                               data=data, timeout=20)
    except Exception as e:
        _log("telegram fail: %s" % e)


def _load():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"streak": 0, "last_action": 0}


def _save(s):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


def main():
    avail = _avail_mb()
    st = _load()

    if avail >= THRESHOLD_MB:
        if st.get("streak"):
            st["streak"] = 0
            _save(st)
        return

    st["streak"] = st.get("streak", 0) + 1
    _log("DUSUK RAM: %d MB (streak %d/%d)" % (avail, st["streak"], NEED_STREAK))
    now = time.time()

    if st["streak"] >= NEED_STREAK and (now - st.get("last_action", 0)) > COOLDOWN_S:
        mems = {s: _mem_bytes(s) for s in SVCS}
        hog = max(mems, key=lambda s: mems[s])
        hog_mb = mems[hog] // 1048576 if mems[hog] > 0 else 0
        try:
            subprocess.run(["systemctl", "restart", hog], timeout=60)
            _log("AKSIYON: %s restart (%d MB yiyordu) — avail %d MB" % (hog, hog_mb, avail))
            _tg("🧹 RAM BEKÇİSİ: boş RAM %d MB'a düştü → en çok bellek yiyen '%s' (%d MB) "
                "otomatik yeniden başlatıldı; kutu donmadan kurtarıldı. İşlem gerekmez."
                % (avail, hog, hog_mb))
        except Exception as e:
            _log("RESTART HATA %s: %s" % (hog, e))
        st["last_action"] = now
        st["streak"] = 0

    _save(st)


if __name__ == "__main__":
    main()
