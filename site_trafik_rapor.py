#!/usr/bin/env python3
"""
site_trafik_rapor.py — smartmoneyradar.app günlük trafik özeti → admin Telegram DM (2 Eki 2026).

Her akşam 22:30 TR (cron 30 19 * * * UTC), diğer akşam mesajlarıyla aynı saatte, TEK mesaj.
Kaynaklar (salt okur):
  • nginx access.log (+ .1): sayfa açılışı, kaynak (X / Google / doğrudan), saatlik yoğunluk,
    X'in paylaşım kartını okuması, paylaşım linkine tıklama. Bot/test istekleri ayıklanır.
    Site Cloudflare arkasında → IP gerçek değil; "kişi" ≈ farklı tarayıcı imzası (yaklaşık).
  • free_gate_store.json: ücretsiz ziyaretçilerin açtığı hisseler, davet linki / ödül sayıları.
Kullanım:  python site_trafik_rapor.py            → gönderir
           python site_trafik_rapor.py --kuru     → sadece ekrana yazar
"""
import collections
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
LOGS = ["/var/log/nginx/access.log.1", "/var/log/nginx/access.log"]
STORE = os.path.join(ROOT, "free_gate_store.json")
ADMIN_CHAT_ID = "1034525990"
TR = timezone(timedelta(hours=3))

_SATIR = re.compile(r'\S+ \S+ \S+ \[([^\]]+)\] "(\S+) (\S+)[^"]*" (\d+) \d+ "([^"]*)" "([^"]*)"')
_BOT = re.compile(r"bot|spider|crawl|python|aiohttp|curl|wget|preview|facebookexternal|Claude/|HeadlessChrome", re.I)


def _oku(yol: str) -> list[str]:
    try:
        with open(yol, encoding="utf-8", errors="ignore") as f:
            return f.read().splitlines()
    except PermissionError:
        try:
            r = subprocess.run(["sudo", "-n", "cat", yol], capture_output=True, timeout=30)
            return r.stdout.decode("utf-8", "ignore").splitlines()
        except Exception:
            return []
    except OSError:
        return []


def _say(satirlar, bas: datetime, son: datetime) -> dict:
    s = {"sayfa": 0, "tarayici": set(), "kaynak": collections.Counter(), "saat": collections.Counter(),
         "x_kart": 0, "p_tik": 0}
    for l in satirlar:
        m = _SATIR.match(l)
        if not m:
            continue
        zaman, meth, yol, kod, ref, ua = m.groups()
        try:
            t = datetime.strptime(zaman, "%d/%b/%Y:%H:%M:%S %z")
        except ValueError:
            continue
        if not (bas <= t < son):
            continue
        if "Twitterbot" in ua:
            if yol.startswith("/p/"):
                s["x_kart"] += 1
            continue
        if _BOT.search(ua):
            continue
        if yol.startswith("/p/"):
            s["p_tik"] += 1
        if meth == "GET" and (yol == "/" or yol.startswith("/?")) and kod == "200":
            s["sayfa"] += 1
            s["tarayici"].add(ua)
            s["saat"][t.astimezone(TR).hour] += 1
            host = ref.split("/")[2] if "//" in ref else ""
            if host in ("t.co", "x.com", "twitter.com", "mobile.twitter.com"):
                s["kaynak"]["X"] += 1
            elif "google." in host:
                s["kaynak"]["Google"] += 1
            elif host in ("", "-"):
                s["kaynak"]["doğrudan/uygulama"] += 1
            elif "smartmoneyradar" in host:
                s["kaynak"]["site içi"] += 1
            else:
                s["kaynak"][host] += 1
    return s


def rapor() -> str:
    simdi = datetime.now(TR)
    bugun_bas = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    dun_bas, dun_ayni = bugun_bas - timedelta(days=1), simdi - timedelta(days=1)
    satirlar = []
    for y in LOGS:
        satirlar += _oku(y)
    b = _say(satirlar, bugun_bas, simdi)
    d = _say(satirlar, dun_bas, dun_ayni)

    def fark(x, y):
        if not y:
            return ""
        p = (x / y - 1) * 100
        return f" ({'▲' if p >= 0 else '▼'} %{abs(p):.0f} dün bu saate göre)"

    satir = [f"📊 SİTE TRAFİĞİ · {simdi:%d.%m} (00:00–{simdi:%H:%M})", ""]
    satir.append(f"👀 Sayfa açılışı: {b['sayfa']}{fark(b['sayfa'], d['sayfa'])}")
    satir.append(f"👤 Farklı tarayıcı (≈kişi): {len(b['tarayici'])} · dün bu saate: {len(d['tarayici'])}")
    if b["kaynak"]:
        satir.append("🔗 Nereden: " + " · ".join(f"{k} {v}" for k, v in b["kaynak"].most_common(5)))
    if b["saat"]:
        h, c = b["saat"].most_common(1)[0]
        satir.append(f"⏰ En yoğun saat: {h:02d}:00–{h + 1:02d}:00 ({c} açılış)")

    try:
        with open(STORE, encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError):
        db = {}
    now = time.time()
    hisse = collections.Counter()
    acan = 0
    for v in (db.get("cookies") or {}).values():
        if now - v.get("ts", 0) < 86400:
            acan += 1
            for t in (v.get("tickers") or ([v["ticker"]] if v.get("ticker") else [])):
                hisse[t] += 1
    if hisse:
        satir.append("")
        satir.append(f"📈 Ücretsiz ziyaretçinin açtığı hisseler ({acan} tarayıcı, son 24s):")
        satir.append("   " + " · ".join(f"{t} {c}" for t, c in hisse.most_common(8)))

    davetli = sum(1 for v in (db.get("davetli") or {}).values() if now - v.get("ts", 0) < 86400)
    odul = sum(1 for L in (db.get("bonus") or {}).values() for x in L if now - x.get("ts", 0) < 86400)
    satir.append("")
    satir.append(f"𝕏 Paylaşım: X kartı okudu {b['x_kart']} · paylaşım linkine tıklayan {b['p_tik']} · "
                 f"linkle gelen yeni ziyaretçi {davetli} · verilen +1 hisse ödülü {odul}")
    satir.append("")
    satir.append("ℹ️ Kişi sayısı yaklaşık (site Cloudflare arkasında). Kesin tekil sayı: Clarity / GA.")
    return "\n".join(satir)


def gonder(text: str) -> bool:
    import requests
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        for p in (os.path.join(ROOT, "telegram_config.json"),):
            try:
                tok = json.load(open(p, encoding="utf-8"))["bot_token"]
            except Exception:
                pass
    if not tok:
        print("[site_trafik] token yok")
        return False
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": ADMIN_CHAT_ID, "text": text, "disable_web_page_preview": True},
                      timeout=25)
    return r.status_code == 200


if __name__ == "__main__":
    metin = rapor()
    print(metin)
    if "--kuru" not in sys.argv:
        ok = gonder(metin)
        print(f"[site_trafik] {datetime.now(TR):%Y-%m-%d %H:%M} gönderildi={ok}")
        sys.exit(0 if ok else 1)
