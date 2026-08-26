#!/usr/bin/env python3
"""
free_gate.py — Anonim tadımlık kapısı (statik site için).
Çerez (ana kimlik) + IP (suistimal freni) ile: 24 saatte 1 ücretsiz hisse analizi.

Tasarım:
  • Çerez `smr_fid` (rastgele, ~400 gün) = tarayıcı kimliği → 24s'de 1 farklı hisse.
  • Aynı hisseyi tekrar açmak serbest; farklı hisse → kilit.
  • IP = sadece suistimal freni: IP başına 24s'de max IP_CAP (yüksek → CGNAT/mobil ortak IP'de
    masum kullanıcı bloklanmaz; tek makineyle çerez silip 1000x denemeyi durdurur).
  • Endeks (XU/XB) her zaman serbest, sayılmaz.

Endpoint:  GET /api/free-check?ticker=THYAO   →  {"allowed": true/false, "reason": "..."}

Çalıştır (lokal):  python free_gate.py        (port 8090)
Prod: systemd servis + nginx /api/ → 127.0.0.1:8090 (aynı origin, CORS gerekmez).
"""
import json
import os
import re
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT     = int(os.environ.get("FREE_GATE_PORT", "8090"))
STORE    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "free_gate_store.json")
WINDOW   = 24 * 3600     # 24 saat
IP_CAP   = 10            # IP başına 24s'de max ücretsiz analiz (suistimal freni — 21 Haz 30→10)
SUB_IP_CAP = 5           # IP başına 24s'de max email kaydı (çöp-mail spam freni)
COOKIE   = "smr_fid"

# Çöp/tek-kullanımlık mail filtresi (21 Haz 2026) — gerçek sağlayıcılar (gmail/mail.com) engellenmez
_JUNK_DOMAINS = {
    "example.com", "test.com", "mailinator.com", "yopmail.com", "guerrillamail.com",
    "10minutemail.com", "tempmail.com", "temp-mail.org", "trashmail.com", "getnada.com",
    "sharklasers.com", "maildrop.cc", "dispostable.com", "throwawaymail.com", "fakeinbox.com",
}
_JUNK_LOCAL = {"test", "asdf", "demo", "example", "xxx", "aaa", "qwe", "qwerty", "abc",
               "noreply", "no-reply", "admin", "deneme", "123"}
_EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")


def _valid_email(email):
    if not email or len(email) > 120 or email.count("@") != 1:
        return False
    if not _EMAIL_RE.match(email):
        return False
    local, dom = email.split("@")
    return dom not in _JUNK_DOMAINS and local not in _JUNK_LOCAL


def _load():
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"cookies": {}, "ips": {}}


def _save(d):
    try:
        with open(STORE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _client_ip(self):
        return ((self.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
                or self.headers.get("X-Real-Ip", "")
                or self.client_address[0])

    def _handle_subscribe(self, u):
        """E-posta bekleme listesi (lead) topla → email_leads.json. Çöp-mail + tekilleştirme
        + IP başına günlük limit ile spam'e karşı korumalı."""
        email = (parse_qs(u.query).get("email", [""])[0] or "").strip().lower()
        ip = self._client_ip()
        ok = False
        if _valid_email(email):
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "email_leads.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    leads = json.load(f)
            except Exception:
                leads = []
            now = int(time.time())
            if any(str(x.get("email", "")).lower() == email for x in leads):
                ok = True   # zaten kayıtlı → sessiz kabul (tekrar ekleme)
            elif sum(1 for x in leads if x.get("ip") == ip and now - x.get("ts", 0) < WINDOW) >= SUB_IP_CAP:
                ok = False  # bu IP bugün çok email girdi → spam freni
            else:
                leads.append({"email": email, "ip": ip, "ts": now,
                              "ua": (self.headers.get("User-Agent", "") or "")[:200]})
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(leads, f, ensure_ascii=False, indent=2)
                    ok = True
                except Exception:
                    pass
        body = json.dumps({"ok": ok}).encode("utf-8")
        self.send_response(200); self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/subscribe":
            return self._handle_subscribe(u)
        if u.path != "/api/free-check":
            self.send_response(404); self._cors(); self.end_headers(); return

        ticker = (parse_qs(u.query).get("ticker", [""])[0] or "").upper()

        # çerez
        fid, set_cookie = "", False
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            part = part.strip()
            if part.startswith(COOKIE + "="):
                fid = part[len(COOKIE) + 1:]
        if not fid:
            fid, set_cookie = uuid.uuid4().hex, True

        # ip (nginx arkasında X-Forwarded-For)
        ip = ((self.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
              or self.headers.get("X-Real-Ip", "")
              or self.client_address[0])

        now = time.time()
        db  = _load()
        allowed, reason = True, "ok"

        # endeks → serbest, sayma
        if ticker and not (ticker.startswith("XU") or ticker.startswith("XB")):
            crec = db["cookies"].get(fid)
            if crec and (now - crec.get("ts", 0) < WINDOW):
                if crec.get("ticker") == ticker:
                    allowed, reason = True, "same"        # kendi ücretsiz hissesini tekrar açıyor
                else:
                    allowed, reason = False, "cookie_used"
            else:
                iplist = [t for t in db["ips"].get(ip, []) if now - t < WINDOW]
                if len(iplist) >= IP_CAP:
                    allowed, reason = False, "ip_cap"
                else:
                    allowed, reason = True, "new"
                    db["cookies"][fid] = {"ticker": ticker, "ts": now}
                    iplist.append(now)
                    db["ips"][ip] = iplist
                    # eski çerez kayıtlarını ara sıra buda (dosya şişmesin)
                    if len(db["cookies"]) > 5000:
                        db["cookies"] = {k: v for k, v in db["cookies"].items()
                                         if now - v.get("ts", 0) < WINDOW}
                    _save(db)

        body = json.dumps({"allowed": allowed, "reason": reason}).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        if set_cookie:
            self.send_header("Set-Cookie",
                             f"{COOKIE}={fid}; Max-Age=34560000; Path=/; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"[free_gate] dinleniyor :{PORT}  (store: {STORE})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
