#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kanit Tweet — YAYIN-ONCE dogruluk dongusu (er_B11 izleme listesi).

Felsefe: Bir makbuz ancak ONCEDEN public, tarih damgali bir cagri varsa makbuzdur.
Yoksa "sonradan reklam" olur ve itibar catlar. Bu yuzden:
  1) CAGRI: er_B11 taze sinyalinden 'Radarimda X' izleme taslagi -> admin Telegram.
     Ayni anda kanit_calls.json defterine yazilir (status=called).
  2) MAKBUZ: gunler sonra, SADECE defterdeki cagrilardan, olgunlasip TUTMUS olani
     signal_results'tan okuyup makbuz taslagi. Makbuz, kullaniciya 'orijinal cagri
     tweetini alintila' der -> insan, cagriyi gercekten attigini dogrulayan son kapi.
  3) KARNE: ara ara (>=N olgun cagri varsa) DURUST toplu karne — kacıranlar dahil.

Neden er_B11: temiz backtest'te piyasa duserken bile pozitif kalan tek buyuk-ornekli
tarama (N=529, %49 isabet, +%4.5 20g). Kaynak: evidence.py SCANNER_TIER_MAP.

Karar onceligi her tetikte: (1) karne zamani+yeterli olgun cagri -> KARNE
                            (2) olgun+tutmus+raporlanmamis cagri -> MAKBUZ
                            (3) aksi halde -> yeni CAGRI

patron.db SALT-OKUNUR acilir (backtest_runner 19:30 yazarken kilit catismasi olmasin).

CLI: --test (kapi yok, ornek uret+gonder, deftere DOKUNMA)
     --dry  (uret+logla, GONDERME, deftere DOKUNMA)
     --call / --receipt / --karne (ilgili modu ZORLA, --dry ile birlikte kullan)
"""

import os
import sys
import json
import sqlite3
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path(__file__).parent
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
except Exception:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("kanit")

STATE_PATH = BASE / "kanit_state.json"
CALLS_PATH = BASE / "kanit_calls.json"

TEST = "--test" in sys.argv
DRY = "--dry" in sys.argv
FORCE = None
for m in ("--call", "--receipt", "--karne"):
    if m in sys.argv:
        FORCE = m[2:]

SCAN_TYPE = "er_B11"
SCAN_LABEL = "Erken Radar — Tepe Yakını Sıkışma"
# evidence.py'den (temiz 20g backtest, N=529): durust rozet cumlesi
SCANNER_STATS = "529 sinyal · %49 isabet · piyasa düşerken bile ortalama +%4.5"


# ---------- I/O ----------
def load_json(p, default):
    if not p.exists():
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, data):
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def db_connect(cfg):
    dbp = cfg.get("db_path", "../patron.db")
    p = Path(dbp)
    if not p.is_absolute():
        p = (BASE / p).resolve()
    # SALT-OKUNUR ac
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=15)


def norm(sym):
    return (sym or "").replace(".IS", "").strip().upper()


# ---------- Telegram ----------
def tg_send(chat_id, text):
    import requests
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN yok — gonderilemedi.")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat_id, "text": text,
                                "disable_web_page_preview": True}, timeout=25)
        if r.status_code != 200:
            log.warning(f"Telegram HTTP {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        log.warning(f"Telegram fail: {e}")
        return False


# ---------- Cagri secimi ----------
def pick_new_call(con, calls):
    """En taze scan_date'ten, en yuksek konviksiyonlu, daha once cagrilmamis er_B11 sinyali."""
    cur = con.cursor()
    last = cur.execute("SELECT MAX(scan_date) FROM scan_signals WHERE scan_type=?", (SCAN_TYPE,)).fetchone()[0]
    if not last:
        return None
    rows = cur.execute("""SELECT scan_date, symbol, entry_price, score, f_master_score, f_rsi
                          FROM scan_signals WHERE scan_type=? AND scan_date=?
                          ORDER BY COALESCE(f_master_score,0) DESC, COALESCE(score,0) DESC""",
                       (SCAN_TYPE, last)).fetchall()
    # son 30 gunde cagrilmis sembolleri ele
    recent_cut = (date.today() - timedelta(days=30)).isoformat()
    called_syms = {c["symbol"] for c in calls["cagrilar"] if c["called_on"] >= recent_cut}
    for scan_date, symbol, entry, score, ms, rsi in rows:
        s = norm(symbol)
        if s in called_syms:
            continue
        return {"symbol": s, "signal_date": scan_date, "entry_price": entry,
                "score": score, "f_master_score": ms, "f_rsi": rsi}
    return None


def lookup_outcome(con, sym, signal_date):
    """signal_results'tan bu cagrinin olgunlasmis getirisi (ret_5g/10g)."""
    cur = con.cursor()
    row = cur.execute("""SELECT ret_5g, ret_10g, hit_10g, max_gain_20g
                         FROM signal_results
                         WHERE scan_type=? AND signal_date=?
                               AND REPLACE(UPPER(symbol),'.IS','')=?
                         ORDER BY id DESC LIMIT 1""",
                      (SCAN_TYPE, signal_date, sym)).fetchone()
    return row  # (ret_5g, ret_10g, hit_10g, max_gain_20g) veya None


def find_matured_receipt(con, cfg, calls):
    """Olgunlasmis (>=min_matur_gun), TUTMUS, raporlanmamis ilk cagriyi bul."""
    min_g = cfg.get("min_matur_gun", 5)
    cut = (date.today() - timedelta(days=min_g)).isoformat()
    thr5 = cfg.get("makbuz_esik_ret5", 5.0)
    thr10 = cfg.get("makbuz_esik_ret10", 8.0)
    for c in calls["cagrilar"]:
        if c.get("reported") or c["signal_date"] > cut:
            continue
        out = lookup_outcome(con, c["symbol"], c["signal_date"])
        if not out:
            continue
        r5, r10, h10, mg = out
        # tutmus mu? (5g ya da 10g esigi)
        best = None
        if r10 is not None and r10 >= thr10:
            best = ("10 gün", r10)
        elif r5 is not None and r5 >= thr5:
            best = ("5 gün", r5)
        if best:
            return c, best
    return None, None


def build_karne(con, cfg, calls):
    """Olgunlasmis TUM cagrilarin durust toplu karnesi (kacıranlar dahil)."""
    min_g = cfg.get("min_matur_gun", 5)
    cut = (date.today() - timedelta(days=min_g)).isoformat()
    rets, wins, best = [], 0, None
    for c in calls["cagrilar"]:
        if c["signal_date"] > cut:
            continue
        out = lookup_outcome(con, c["symbol"], c["signal_date"])
        if not out:
            continue
        r10 = out[1] if out[1] is not None else out[0]
        if r10 is None:
            continue
        rets.append(r10)
        if r10 > 0:
            wins += 1
        if best is None or r10 > best[1]:
            best = (c["symbol"], r10)
    return rets, wins, best


# ---------- Taslak bicimleri ----------
BANNER = "📡 FIRSAT TARAMA RADARI"
CIZGI = "━━━━━━━━━━━━━━━"

_AYLAR = {"01":"Ocak","02":"Şubat","03":"Mart","04":"Nisan","05":"Mayıs","06":"Haziran",
          "07":"Temmuz","08":"Ağustos","09":"Eylül","10":"Ekim","11":"Kasım","12":"Aralık"}


def _tarih_uzun(iso):
    y, m, d = iso.split("-")
    return f"{int(d)} {_AYLAR.get(m, m)}"


def fmt_call(c):
    """(ic_not, paylasim) — paylasim gruba/X'e forward edilebilir, ic_not sana ozel."""
    ic_not = "👇 Fırsat Tarama Radarı — çağrı. Gruba forward'la ya da X'e at."
    paylasim = (
        f"{BANNER}\n"
        f"{CIZGI}\n"
        f"Radarımda: {c['symbol']}\n"
        "«Tepe Yakını Sıkışma» sinyali oluştu.\n\n"
        "Bu tarama, piyasa düşerken bile artıda kalabilen türden:\n"
        "529 sinyal · %49 isabet · ortalama +%4.5\n\n"
        f"Garanti değil — izliyorum. ({datetime.now().strftime('%d.%m')})"
    )
    return ic_not, paylasim


def fmt_receipt(c, best):
    sure, ret = best
    tarih = _tarih_uzun(c["signal_date"])
    ic_not = (
        "👇 MAKBUZ taslağı.\n"
        f"⚠️ Bunu SADECE {tarih} tarihli o çağrıyı GERÇEKTEN paylaştıysan at "
        "(grupta/X'te) — en güçlüsü orijinali alıntılamak. Paylaşmadıysan ATMA."
    )
    paylasim = (
        f"{BANNER} · SONUÇ\n"
        f"{CIZGI}\n"
        f"{tarih}'de radarıma aldığım {c['symbol']} — {sure}de +%{ret:.1f}.\n\n"
        "Çağrı ortadaydı, tarih damgalıydı. İşe yarayanı da konuşuruz, tutmayanı da."
    )
    return ic_not, paylasim


def fmt_karne(rets, wins, best):
    n = len(rets)
    avg = sum(rets) / n
    hit = 100 * wins / n
    bsym, bret = best
    ic_not = "👇 Radar karnesi — gruba forward'la ya da X'e at."
    paylasim = (
        f"{BANNER} · KARNE\n"
        f"{CIZGI}\n"
        f"Son {n} izleme çağrısı: %{hit:.0f} artıda, ortalama %{avg:+.1f}.\n"
        f"En iyi: {bsym} %{bret:+.1f}.\n\n"
        "Kaçıranları da sayıyorum — sadece kazananı gösteren tabloya güven olmaz."
    )
    return ic_not, paylasim


# ---------- Main ----------
def main():
    cfg = load_json(STATE_PATH, {})
    calls = load_json(CALLS_PATH, {"cagrilar": [], "karne_sayaci": 0})
    calls.setdefault("cagrilar", [])
    calls.setdefault("karne_sayaci", 0)

    tz = ZoneInfo(cfg.get("tz", "Europe/Istanbul"))
    now = datetime.now(tz)

    # cift-tetik korumasi
    if not TEST and not DRY:
        last = cfg.get("state", {}).get("last_sent_ts")
        if last:
            try:
                if (now - datetime.fromisoformat(last)).total_seconds()/3600 < cfg.get("min_gap_hours", 12):
                    log.info("Yakinda gonderim yapildi — atlaniyor.")
                    return
            except ValueError:
                pass

    try:
        con = db_connect(cfg)
    except Exception as e:
        log.error(f"DB acilamadi: {e}")
        return

    msg = None
    mode = None
    new_call = None
    receipt_call = None

    # --- KARNE (opsiyonel, oncelik) ---
    karne_her = cfg.get("karne_her_n_gonderim", 6)
    do_karne = (FORCE == "karne") or (FORCE is None and karne_her and
                calls["karne_sayaci"] > 0 and calls["karne_sayaci"] % karne_her == 0)
    if do_karne:
        rets, wins, best = build_karne(con, cfg, calls)
        if len(rets) >= cfg.get("karne_min_cagri", 5):
            msg, mode = fmt_karne(rets, wins, best), "karne"

    # --- MAKBUZ ---
    if msg is None and FORCE in (None, "receipt"):
        rc, best = find_matured_receipt(con, cfg, calls)
        if rc:
            receipt_call = rc
            msg, mode = fmt_receipt(rc, best), "receipt"

    # --- CAGRI (varsayilan) ---
    if msg is None and FORCE in (None, "call"):
        new_call = pick_new_call(con, calls)
        if new_call:
            msg, mode = fmt_call(new_call), "call"

    con.close()

    if msg is None:
        log.warning("Uretilebilir icerik yok (taze er_B11 sinyali ya da olgun cagri yok).")
        return

    log.info(f"Mod: {mode}")
    ic_not, paylasim = msg
    if DRY:
        log.info("DRY-RUN — gonderilmedi:\n[İÇ NOT]\n" + ic_not +
                 "\n\n[PAYLAŞIM — forward edilir]\n" + paylasim)
        return

    admin = str(cfg["admin_chat_id"])
    tg_send(admin, ic_not)                 # once ic not (forward edilmez)
    if not tg_send(admin, paylasim):       # sonra temiz paylasim mesaji
        log.error("Telegram gonderimi basarisiz — defter guncellenmedi.")
        return
    log.info(f"{mode} taslagi gonderildi (2 mesaj: ic not + paylasim).")

    if TEST:
        return

    # defter + state guncelle
    if mode == "call" and new_call:
        calls["cagrilar"].append({
            "symbol": new_call["symbol"], "signal_date": new_call["signal_date"],
            "entry_price": new_call["entry_price"], "called_on": now.date().isoformat(),
            "reported": False})
    elif mode == "receipt" and receipt_call:
        for c in calls["cagrilar"]:
            if c["symbol"] == receipt_call["symbol"] and c["signal_date"] == receipt_call["signal_date"]:
                c["reported"] = True
                break
    calls["karne_sayaci"] = calls.get("karne_sayaci", 0) + 1
    save_json(CALLS_PATH, calls)
    cfg.setdefault("state", {})["last_sent_ts"] = now.isoformat()
    save_json(STATE_PATH, cfg)
    log.info("Defter + state guncellendi.")


if __name__ == "__main__":
    main()
