"""kirilim_karne.py — KIRILIM DURUMU KARNESİ (tek seferlik ölçüm aracı)

Soru: sinyal doğduğu andaki KIRILIM DURUMU (f_breakout_state) ileri getiriyi
öngörüyor mu?  Kovalar:
    0 = boyundan uzak · 1 = boynu test ediyor · 2 = hacimli kırılım · 3 = gap kırılım

Kaynak: patron.db → scan_signals JOIN signal_returns
Çıktı : konsol tablo + logs/kirilim_karne.md

⚠ Bu araç KURAL KOYMAZ, sadece ölçer. Eşik/ağırlık uydurmak yasak
   (bkz. feedback_extrapolation_yasak).
"""
import sqlite3
import statistics as st
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # Windows cp1254 konsolu icin
except Exception:
    pass

DB = Path(__file__).with_name("patron.db")
OUT = Path(__file__).with_name("logs") / "kirilim_karne.md"
HORIZONS = (5, 10, 20)
STATE_AD = {0: "0 uzak", 1: "1 boyun testi", 2: "2 hacimli kırılım", 3: "3 gap kırılım"}
MIN_N = 30          # altında yorum yapılmaz


def _cek(con, ekstra_where="", params=()):
    """(state, day_offset, return_pct, scan_type) satırları."""
    sql = f"""
        SELECT s.f_breakout_state, r.day_offset, r.return_pct, s.scan_type
        FROM scan_signals s
        JOIN signal_returns r ON r.signal_id = s.id
        WHERE s.f_breakout_state IS NOT NULL
          AND r.return_pct IS NOT NULL
          AND r.day_offset IN ({','.join(str(h) for h in HORIZONS)})
          {ekstra_where}
    """
    return con.execute(sql, params).fetchall()


def _ozet(getiriler):
    n = len(getiriler)
    if n == 0:
        return None
    hit = sum(1 for g in getiriler if g > 0) / n * 100
    return dict(
        n=n,
        ort=st.mean(getiriler),
        med=st.median(getiriler),
        hit=hit,
    )


def _tablo(baslik, kovalar, satir_ad):
    """kovalar: {anahtar: {gun: [getiri...]}}"""
    lines = [f"\n### {baslik}\n",
             "| " + satir_ad + " | Gün | N | Ort % | Medyan % | İsabet % |",
             "|---|---|---|---|---|---|"]
    for anahtar in sorted(kovalar):
        for gun in HORIZONS:
            o = _ozet(kovalar[anahtar].get(gun, []))
            if not o:
                continue
            zayif = " ⚠az" if o["n"] < MIN_N else ""
            lines.append(
                f"| {anahtar} | T+{gun} | {o['n']}{zayif} | "
                f"{o['ort']:+.2f} | {o['med']:+.2f} | {o['hit']:.1f} |"
            )
    return lines


def main():
    con = sqlite3.connect(DB)
    rows = _cek(con)

    # --- 1) Genel: durum × ufuk
    kovalar = {}
    for state, gun, ret, _st in rows:
        ad = STATE_AD.get(state, f"? {state}")
        kovalar.setdefault(ad, {}).setdefault(gun, []).append(ret)

    # --- 2) Taban çizgisi (tüm sinyaller, durum ayrımsız)
    taban = {}
    for _state, gun, ret, _st in rows:
        taban.setdefault("TÜMÜ", {}).setdefault(gun, []).append(ret)

    # --- 3) En kalabalık taramalarda durum ayrımı (rejim/tarama etkisi ayıklansın)
    say = {}
    for state, gun, ret, sc in rows:
        say[sc] = say.get(sc, 0) + 1
    en_buyuk = [k for k, _ in sorted(say.items(), key=lambda x: -x[1])[:4]]

    per_scan = {}
    for state, gun, ret, sc in rows:
        if sc not in en_buyuk:
            continue
        ad = f"{sc} · {STATE_AD.get(state, state)}"
        per_scan.setdefault(ad, {}).setdefault(gun, []).append(ret)

    tarih = con.execute(
        "SELECT MIN(scan_date), MAX(scan_date) FROM scan_signals "
        "WHERE f_breakout_state IS NOT NULL"
    ).fetchone()

    out = [
        "# KIRILIM DURUMU KARNESİ",
        "",
        f"- Veri aralığı: **{tarih[0]} → {tarih[1]}** (tek rejim — yükselen tape)",
        f"- Eşleşmiş satır: **{len(rows)}**",
        f"- ⚠ N < {MIN_N} olan kovalar yorumlanmaz.",
        "- ⚠ Tek rejimde ölçüldü; düşen piyasada tekrar koşulmalı.",
    ]
    out += _tablo("Taban çizgisi", taban, "Kova")
    out += _tablo("Kırılım durumuna göre", kovalar, "Durum")
    out += _tablo("En kalabalık 4 taramada durum ayrımı", per_scan, "Tarama · Durum")

    metin = "\n".join(out)
    print(metin)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(metin, encoding="utf-8")
    print(f"\n→ {OUT}")


if __name__ == "__main__":
    main()
