#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rapid_volume_probe.py — RapidAPI BIST hacim TAZELIK gozlemcisi
Amac (12 Ağu 2026): RapidAPI 'BIST100 15dk gecikmeli' feed'i (tek istekte 626 hisse,
volume_lot + volume_turkish_lira) İş Yatırım hacim yamasının yerine gecebilir mi?
İlk test (12 Ağu 13:10) feed'i DUNDE (11.08 15:27) DONUK gosterdi -> birkac gun gozle.

Calisma: gunde 1 kez (islem gununde, seans ortasi ~13:00 TR) /bist100/prices ceker,
feed'in last_update'ini (taze mi = bugun mu) + 6 referans hissenin hacmini
logs/rapid_volume_probe.jsonl'e ekler. DEADLINE 31 Agu 2026 -> sonra kayit okunur, karar.
UCRETSIZ KOTA 30 istek/AY HARD LIMIT -> gunde 1'den FAZLA CALISTIRMA.
"""
import os, sys, json, datetime, urllib.request

KEY  = os.environ.get("RAPIDAPI_KEY", "8469a7de6emsh2f7bce8c90b6131p11a605jsn4075db361665")
HOST = "bist100-stock-data-15-minutes-late-live.p.rapidapi.com"
URL  = f"https://{HOST}/bist100/prices"
REF  = ["THYAO", "OTKAR", "GARAN", "EREGL", "ASELS", "SASA"]
HERE = os.path.dirname(os.path.abspath(__file__))
LOG  = os.path.join(HERE, "logs", "rapid_volume_probe.jsonl")


def tr_now():
    return (datetime.datetime.utcnow() + datetime.timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")


def _write(rec):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parse(payload):
    """RapidAPI yanitini (dict veya path/str) -> kayit dict'ine cevir."""
    if isinstance(payload, (bytes, str)) and os.path.exists(payload):
        payload = json.load(open(payload, encoding="utf-8"))
    d = payload
    arr = d.get("data") if isinstance(d, dict) else d
    if not arr:
        return {"pull_tr": tr_now(), "error": "bos/veri yok"}
    by = {x.get("code"): x for x in arr}
    refs = {}
    for s in REF:
        x = by.get(s)
        if x:
            refs[s] = {"last": x.get("last"), "vlot": x.get("volume_lot"),
                       "vtl": x.get("volume_turkish_lira"), "lu": x.get("last_update")}
    return {"pull_tr": tr_now(), "n": len(arr),
            "feed_last_update": arr[0].get("last_update"), "refs": refs}


def _isy_volumes(tickers):
    """EOD karsilastirmasi: ayni an İş Yatırım hacmini cek (best-effort, RapidAPI kotasini HARCAMAZ)."""
    out = {}
    try:
        from isyatirim_gateway import robust_isyatirim
    except Exception as e:
        return {"_err": f"import: {e}"}
    for t in tickers:
        sym = t if t.endswith(".IS") else t + ".IS"
        try:
            df, src = robust_isyatirim(sym, period_days=5, tries=2, allow_stale=False,
                                       priority="regular_fetch", max_wait=30.0)
            if df is not None and not df.empty:
                out[t] = {"date": str(df.index[-1].date()),
                          "vol": float(df["Volume"].iloc[-1]), "src": src}
            else:
                out[t] = None
        except Exception as e:
            out[t] = {"_err": str(e)}
    return out


def main(slot="mid"):
    # islem gunu degilse istek harcama
    try:
        sys.path.insert(0, HERE)
        import bist_calendar as bc
        if not bc.is_trading_day(datetime.date.today()):
            print("islem gunu degil, atla"); return
    except Exception:
        pass
    # NOT: RapidAPI, Python'un varsayilan User-Agent'ini 403'ler -> normal UA sart (12 Ağu 2026)
    req = urllib.request.Request(URL, headers={
        "x-rapidapi-host": HOST, "x-rapidapi-key": KEY,
        "User-Agent": "Mozilla/5.0 (compatible; smr-probe/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:
        _write({"pull_tr": tr_now(), "slot": slot, "error": str(e)}); print("hata:", e); return
    rec = parse(d)
    rec["slot"] = slot
    # EOD (18:45) modunda ayni an İş Yatırım hacmini de yan yana logla -> 31 Agu karsilastirma
    if slot == "eod":
        rec["isy"] = _isy_volumes(REF)
    _write(rec)
    fresh = "?"
    lu = rec.get("feed_last_update") or ""
    today = datetime.date.today().strftime("%d.%m.%Y")
    if lu:
        fresh = "TAZE(bugun)" if lu.startswith(today) else f"BAYAT({lu})"
    print("OK", rec.get("pull_tr"), "n=", rec.get("n"), "feed=", lu, "->", fresh)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--parse-test":
        print(json.dumps(parse(sys.argv[2]), ensure_ascii=False, indent=2))
    else:
        slot = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("mid", "eod") else "mid"
        main(slot)
