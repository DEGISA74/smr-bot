#!/usr/bin/env python3
"""leads.py — toplanan e-postaları özetle (kaç farklı kişi geldi, kaynak, son girenler).
Çalıştır: python3 ~/smr/leads.py"""
import json, os
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))

def _load(p, default):
    try:
        with open(os.path.join(BASE, p), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

leads = _load("email_leads.json", [])
free  = _load("free_users.json", {})

uniq = {}
for l in leads:
    e = (l.get("email", "") or "").strip().lower()
    if e:
        uniq.setdefault(e, l.get("source", "waitlist"))
for e in free:
    uniq.setdefault(e.strip().lower(), "showcase_free")

src = Counter(l.get("source", "waitlist") for l in leads)

print("=" * 44)
print(f"  FARKLI KİŞİ (benzersiz e-posta): {len(uniq)}")
print(f"  Toplam kayıt (email_leads)    : {len(leads)}")
print(f"  Ücretsiz araca giren (free)   : {len(free)}")
print(f"  Kaynak dağılımı               : {dict(src)}")
print("=" * 44)
print("Son 20 kayıt:")
for l in leads[-20:]:
    ts = str(l.get("ts", "?"))[:16]
    print(f"  {ts}  {l.get('email','?'):32}  [{l.get('source','waitlist')}]  {l.get('ip','')}")
if not leads and not free:
    print("  (henüz kayıt yok)")
