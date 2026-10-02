#!/usr/bin/env python3
"""
site_trafik_rapor.py — smartmoneyradar.app günlük trafik özeti → admin Telegram DM (2 Eki 2026).

Her akşam 22:30 TR (cron 30 19 * * * UTC), diğer akşam mesajlarıyla aynı saatte, TEK mesaj.
Kaynaklar (salt okur):
  • nginx access.log (+ .1): sayfa açılışı, kaynak (X / Google / doğrudan), saatlik yoğunluk,
    X'in paylaşım kartını okuması, paylaşım linkine tıklama. Bot/test istekleri ayıklanır.
    Site Cloudflare arkasında → IP gerçek değil; "kişi" ≈ farklı tarayıcı imzası (yaklaşık).
  • free_gate_store.json: ücretsiz ziyaretçilerin açtığı hisseler, davet linki / ödül sayıları.
  • free_gate_sure.json: ziyaret başına ekranda kalınan süre (ortalama kalma süresi).
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
SURE_FILE = os.path.join(ROOT, "free_gate_sure.json")   # sitede kalma süresi (app.js → /api/sure)
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


def _sure_ortalama(bas_ts: float, son_ts: float):
    """free_gate_sure.json: ziyaret başına ekranda kalınan saniye. Bu aralıkta başlayan ziyaretler."""
    try:
        with open(SURE_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None, 0
    L = [v[1] for v in d.values() if bas_ts <= v[0] < son_ts and v[1] >= 2]
    if not L:
        return None, 0
    return sum(L) / len(L), len(L)


def _sure_yaz(sn: float) -> str:
    sn = int(round(sn))
    return f"{sn // 60}m {sn % 60:02d}s" if sn >= 60 else f"{sn}s"


def rapor() -> str:
    simdi = datetime.now(TR)
    bugun_bas = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    dun_bas, dun_ayni = bugun_bas - timedelta(days=1), simdi - timedelta(days=1)
    satirlar = []
    for y in LOGS:
        satirlar += _oku(y)
    b = _say(satirlar, bugun_bas, simdi)
    d = _say(satirlar, dun_bas, dun_ayni)

    kisi, kisi_dun = len(b["tarayici"]), len(d["tarayici"])
    kiyas = ""
    if kisi_dun:
        oran = kisi / kisi_dun
        kiyas = (f" (dün bu saatte {kisi_dun} kişi, "
                 + (f"{oran:.1f} katı".replace(".", ",") if oran >= 1.5 else
                    "biraz fazla" if oran > 1.05 else "biraz az" if oran < 0.95 else "aynı") + ")")

    satir = [f"📊 WEB SİTESİ İSTATİSTİKLERİ · {simdi:%d.%m.%Y} saat {simdi:%H:%M} itibariyle", ""]
    satir.append(f"👥 Yaklaşık {kisi} kişi siteye girdi{kiyas}")
    satir.append(f"🔁 Site toplam {b['sayfa']} kez açıldı")
    ort, n = _sure_ortalama(bugun_bas.timestamp(), simdi.timestamp())
    if ort is not None:
        satir.append(f"⏱ Ortalama sitede kalma süresi: {_sure_yaz(ort)}")
    x, g = b["kaynak"].get("X", 0), b["kaynak"].get("Google", 0)
    parca = [p for p in (f"{x} kez X'teki bir linkten gelindi" if x else "",
                         f"{g} kez Google'dan" if g else "") if p]
    if parca:
        satir.append("📲 " + " · ".join(parca))
    if b["saat"]:
        h, _ = b["saat"].most_common(1)[0]
        satir.append(f"🕙 En kalabalık saat: {h:02d}:00–{h + 1:02d}:00")

    try:
        with open(STORE, encoding="utf-8") as f:
            db = json.load(f)
    except (OSError, ValueError):
        db = {}
    now = time.time()
    hisse = collections.Counter()
    for v in (db.get("cookies") or {}).values():
        if now - v.get("ts", 0) < 86400:
            for t in (v.get("tickers") or ([v["ticker"]] if v.get("ticker") else [])):
                hisse[t] += 1
    if hisse:
        satir.append("")
        satir.append("🔎 En çok incelenen hisseler: "
                     + ", ".join(f"{t} ({c})" if c > 1 else t for t, c in hisse.most_common(6)))

    davetli = sum(1 for v in (db.get("davetli") or {}).values() if now - v.get("ts", 0) < 86400)
    odul = sum(1 for L in (db.get("bonus") or {}).values() for x_ in L if now - x_.get("ts", 0) < 86400)
    if b["p_tik"] or davetli or odul:
        satir.append("")
        satir.append(f"🎁 Paylaşılan linklere {b['p_tik']} kez tıklandı · bunlardan {davetli} kişi siteye ilk kez geldi"
                     + (f" → paylaşanlara {odul} bedava hisse hakkı verildi" if odul else ""))
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
