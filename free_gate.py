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
import hashlib
import hmac
import json
import os
import secrets
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
BONUS_CAP = 3            # 2 Eki 2026 — paylaşım ödülü: 24 saatte en fazla +3 hisse

# 2 Eki 2026 — ADMIN: gizli anahtarlı giriş (kullanıcı kararı). Anahtar sunucuda
# free_gate_admin.key dosyasında (600 izin, git'e girmez). /api/admin sayfasına bir kez
# girilir → HttpOnly "smr_admin" çerezi (400 gün). Admin için free-check sayılmaz, her şey açık.
ADMIN_COOKIE = "smr_admin"
ADMIN_KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "free_gate_admin.key")


def _admin_token():
    """Çerezde saklanan değer: anahtarın kendisi değil, özeti (anahtar çerezde dolaşmaz)."""
    try:
        with open(ADMIN_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        return None
    return hashlib.sha256(("smr-admin|" + key).encode("utf-8")).hexdigest() if len(key) >= 16 else None


def _admin_key_ok(entered):
    try:
        with open(ADMIN_KEY_FILE, encoding="utf-8") as f:
            key = f.read().strip()
    except OSError:
        return False
    return len(key) >= 16 and hmac.compare_digest(entered.strip().encode("utf-8"), key.encode("utf-8"))


_ADMIN_FORM = """<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>SMR Admin</title>
<style>body{margin:0;background:#0b1020;color:#e6ebf5;font-family:system-ui,sans-serif;display:flex;
min-height:100vh;align-items:center;justify-content:center}form{background:#121a2e;border:1px solid #24304d;
border-radius:10px;padding:24px;width:min(340px,90vw)}input,button{width:100%;box-sizing:border-box;
padding:12px;border-radius:6px;font-size:15px}input{background:#0b1020;color:#e6ebf5;border:1px solid #334155;
margin:10px 0}button{background:#1d9e75;color:#fff;border:0;font-weight:700}p{color:#9aa6c2;font-size:13px}</style>
</head><body><form method="post" action="/api/admin"><strong>Smart Money Radar · Admin</strong>
<p>{MESAJ}</p><label for="k">Gizli anahtar</label><input id="k" name="anahtar" type="password" autocomplete="current-password" required>
<button type="submit">Giriş</button></form></body></html>"""


def _admin_form(mesaj):
    return _ADMIN_FORM.replace("{MESAJ}", mesaj)

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

    def _cookie(self, name):
        for part in (self.headers.get("Cookie", "") or "").split(";"):
            part = part.strip()
            if part.startswith(name + "="):
                return part[len(name) + 1:]
        return ""

    def _secure(self):
        """Canlıda (https, gerçek alan adı) çerez 'Secure'; lokal http denemesinde değil."""
        host = (self.headers.get("Host", "") or "").split(":")[0]
        return "; Secure" if host not in ("localhost", "127.0.0.1") else ""

    def _is_admin(self):
        tok = _admin_token()
        val = self._cookie(ADMIN_COOKIE)
        return bool(tok and val and hmac.compare_digest(val, tok))

    def _html(self, code, html, extra_headers=()):
        body = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/admin":
            self.send_response(404); self.end_headers(); return
        n = min(int(self.headers.get("Content-Length", "0") or 0), 2048)
        form = parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
        if _admin_key_ok((form.get("anahtar", [""])[0] or "")):
            self.send_response(303)
            self.send_header("Set-Cookie", f"{ADMIN_COOKIE}={_admin_token()}; Max-Age=34560000; Path=/; "
                                           f"HttpOnly{self._secure()}; SameSite=Lax")
            self.send_header("Location", "/"); self.send_header("Content-Length", "0")
            self.end_headers(); return
        time.sleep(1.0)   # kaba tahmin freni
        self._html(200, _admin_form("Anahtar hatalı."))

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
        if u.path == "/api/admin":
            return self._html(200, _admin_form("Zaten admin olarak girişlisin." if self._is_admin()
                                               else "Bu tarayıcıyı admin olarak tanıtmak için anahtarı gir."))
        if u.path == "/api/admin-cikis":
            return self._html(200, _admin_form("Admin çıkışı yapıldı."),
                              [("Set-Cookie", f"{ADMIN_COOKIE}=; Max-Age=0; Path=/; HttpOnly{self._secure()}; SameSite=Lax")])
        if u.path not in ("/api/free-check", "/api/davet-kodu", "/api/davet"):
            self.send_response(404); self._cors(); self.end_headers(); return

        q = parse_qs(u.query)
        ticker = (q.get("ticker", [""])[0] or "").upper()

        # çerez (yoksa bu istek = sitede İLK görülme)
        fid, set_cookie = self._cookie(COOKIE), False
        if not fid:
            fid, set_cookie = uuid.uuid4().hex, True

        # ip (nginx arkasında X-Forwarded-For)
        ip = ((self.headers.get("X-Forwarded-For", "") or "").split(",")[0].strip()
              or self.headers.get("X-Real-Ip", "")
              or self.client_address[0])

        now = time.time()
        db  = _load()
        for _k in ("cookies", "ips", "kodlar", "fid_kod", "bonus", "davetli"):
            db.setdefault(_k, {})
        admin = self._is_admin()
        out = {}

        if u.path == "/api/davet-kodu":
            # 2 Eki 2026 — PAYLAŞ: her tarayıcıya kısa, rastgele kod (çerez kimliği dışarı sızmaz).
            kod = db["fid_kod"].get(fid)
            if not kod:
                kod = secrets.token_urlsafe(6)[:7]
                while kod in db["kodlar"]:
                    kod = secrets.token_urlsafe(6)[:7]
                db["fid_kod"][fid] = kod
            db["kodlar"][kod] = {"fid": fid, "ip": ip, "ts": now}     # paylaşanın güncel IP'si
            _save(db)
            out = {"kod": kod}

        elif u.path == "/api/davet":
            # Linkle gelen ziyaretçi: YENİ (çerezi yok) + paylaşanla aynı IP değil → paylaşana +1.
            kod = (q.get("d", [""])[0] or "")[:12]
            rec = db["kodlar"].get(kod)
            kabul, neden = False, "gecersiz_kod"
            if rec:
                if not set_cookie:
                    neden = "eski_ziyaretci"
                elif rec.get("ip") == ip:
                    neden = "ayni_baglanti"
                elif rec.get("fid") == fid:
                    neden = "kendisi"
                else:
                    liste = [b for b in db["bonus"].get(rec["fid"], []) if now - b.get("ts", 0) < WINDOW]
                    if len(liste) >= BONUS_CAP:
                        neden = "gunluk_sinir"
                    else:
                        liste.append({"ts": now, "goruldu": False})
                        db["bonus"][rec["fid"]] = liste
                        db["davetli"][fid] = {"kod": kod, "ts": now}
                        kabul, neden = True, "ok"
                _save(db)
            out = {"kabul": kabul, "neden": neden}

        else:
            allowed, reason = True, "ok"
            bonus = [b for b in db["bonus"].get(fid, []) if now - b.get("ts", 0) < WINDOW]
            hak = 1 + min(len(bonus), BONUS_CAP)
            yeni_bonus = sum(1 for b in bonus if not b.get("goruldu"))
            if yeni_bonus:
                for b in bonus:
                    b["goruldu"] = True
                db["bonus"][fid] = bonus
                _save(db)
            extra = {"hak": hak, "kazanilan": len(bonus), "yeni_bonus": yeni_bonus}

            # admin → her şey açık, sayılmaz · endeks → serbest, sayma
            if admin:
                reason = "admin"
            elif ticker and not (ticker.startswith("XU") or ticker.startswith("XB")):
                crec = db["cookies"].get(fid)
                aktif = bool(crec) and (now - crec.get("ts", 0) < WINDOW)
                hisseler = []
                if aktif:
                    hisseler = list(crec.get("tickers") or ([crec["ticker"]] if crec.get("ticker") else []))
                if ticker in hisseler:
                    allowed, reason = True, "same"        # bugünkü hisselerinden birini tekrar açıyor
                elif hisseler and len(hisseler) >= hak:
                    allowed, reason = False, "cookie_used"
                    extra.update({"hak_hisse": hisseler[0], "hak_hisseler": hisseler,
                                  "kalan_dk": int((WINDOW - (now - crec.get("ts", 0))) // 60)})
                else:
                    iplist = [t for t in db["ips"].get(ip, []) if now - t < WINDOW]
                    if not hisseler and len(iplist) >= IP_CAP:
                        allowed, reason = False, "ip_cap"
                    else:
                        allowed, reason = True, "new"
                        db["cookies"][fid] = {"tickers": hisseler + [ticker],
                                              "ts": crec.get("ts", now) if aktif else now}
                        if not hisseler:
                            iplist.append(now)
                            db["ips"][ip] = iplist
                        # eski çerez kayıtlarını ara sıra buda (dosya şişmesin)
                        if len(db["cookies"]) > 5000:
                            db["cookies"] = {k: v for k, v in db["cookies"].items()
                                             if now - v.get("ts", 0) < WINDOW}
                        _save(db)
            out = dict({"allowed": allowed, "reason": reason, "admin": admin}, **extra)

        body = json.dumps(out).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie",
                             f"{COOKIE}={fid}; Max-Age=34560000; Path=/; SameSite=Lax")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print(f"[free_gate] dinleniyor :{PORT}  (store: {STORE})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
