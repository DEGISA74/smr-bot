# -*- coding: utf-8 -*-
"""
flag_health_audit.py — HAFTALIK FLAG SAĞLIK DENETİMİ (4 Tem 2026)
=================================================================
Amaç: "Bozuk sinyal 3 ay sonra fark edildi" filmi tekrarlanmasın.
scan_signals'taki TÜM f_* kolonlarını tarar, 4 tip bozukluk arar:

  1. YENİ ÖLDÜ    — önceki 60 günde doluydu, son 14 günde tamamen boş
                    (scanner bozuldu / sessiz exception)
  2. TEK DEĞER    — doluyor ama herkese aynı şeyi söylüyor (ölçmüyor)
  3. GÜRÜLTÜ      — ikili (0/1) flag'in %50+'sı ateşliyor (ayrım gücü yok,
                    near_ifvg %63 vakası gibi)
  4. ÇELİŞKİ      — mantıken zıt çiftler aynı satırda birlikte 1
                    (TEFAS alım+satım vakası gibi)

Bilinen ölüler (TEFAS/KAP kalıntısı vb.) whitelist'te — her hafta alarm üretmez.
Sonuç: logs/flag_health_report.md + Telegram'a mesaj (yeşil kısa / kırmızı detay).

Çalıştır:  python flag_health_audit.py          (Task Scheduler: Pazar 20:00)
           python flag_health_audit.py --dry    (Telegram'a GÖNDERMEZ — test/smoke)
"""
import sqlite3, json, os, sys, datetime as dt, urllib.request, urllib.parse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE  = os.path.dirname(os.path.abspath(__file__))
DB    = os.path.join(BASE, "patron.db")
ADMIN = 1034525990
DRY   = "--dry" in sys.argv

WINDOW_DAYS  = 14    # "şu an" penceresi
BASELINE_DAYS = 60   # "eskiden doluydu" referansı
MIN_N        = 100   # penceredeki min satır — altındaysa hüküm verme
BLOAT_PCT    = 50.0  # ikili flag ateşleme tavanı
CONTRA_PCT   = 20.0  # zıt çiftin birlikte-1 tavanı

# Bilinen ölüler — kaldırılmış sistemlerin kalıntı kolonları (Oturum 21/24).
# Bunlar için alarm üretme; yeniden dolmaya başlarlarsa o zaman haber ver.
KNOWN_DEAD = {
    "f_tefas_konsensus_alim", "f_tefas_konsensus_satim", "f_tefas_yeni_giris",
    "f_buyback_aktif", "f_buyback_dip_aliyor", "f_threshold_asildi",
    "f_insider_first_buy", "f_kurumsal_anchor",
}

# Bilinen suskunlar/gürültülüler — aktif yazılıyor ama karnesi belli, Eylül 2026
# backtest'ine kadar BİLEREK tutuluyor. Haftalık alarm üretme (yeni bozulanlar üretir).
KNOWN_QUIRK = {
    "f_yabanci_anchor",        # doluyor ama hiç ateşlemiyor (tek değer 0) — temizlik adayı
    "f_near_ifvg",             # %60+ ateşliyor, AI'dan çekildi, Eylül'de ölçülecek
    "f_breaker_block_active",  # %55+ ateşliyor, AI'dan çekildi, Eylül'de ölçülecek
    "f_rel_obv_divergence",    # %58 ateşliyor, karne ZAYIF (+0.05) — Eylül'de karar
}

# Mantıken zıt çiftler — aynı satırda ikisi birden 1 olamaz (olursa hesap bozuk)
CONTRA_PAIRS = [
    ("f_yabanci_giris", "f_yabanci_cikis"),
    ("f_tefas_konsensus_alim", "f_tefas_konsensus_satim"),
]


def tg(msg):
    if DRY:
        print("\n--- TELEGRAM (dry, gönderilmedi) ---\n" + msg)
        return
    try:
        cfg = json.load(open(os.path.join(BASE, "telegram_config.json"), encoding="utf-8"))
        token = cfg["bot_token"]
    except Exception as e:
        print("telegram_config.json okunamadı:", e)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": ADMIN, "text": msg}).encode()
    try:
        urllib.request.urlopen(url, data=data, timeout=25)
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def main():
    today = dt.date.today()
    cut_win  = (today - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    cut_base = (today - dt.timedelta(days=BASELINE_DAYS)).isoformat()

    con = sqlite3.connect(DB)
    cur = con.cursor()

    cols = [r[1] for r in cur.execute("PRAGMA table_info(scan_signals)").fetchall()
            if r[1].startswith("f_")]
    n_win = cur.execute("SELECT COUNT(*) FROM scan_signals WHERE scan_date>=?", (cut_win,)).fetchone()[0]

    problems, revived, lines = [], [], []

    if n_win < MIN_N:
        # Master Scan bu pencerede az çalışmış — heartbeat zaten alarm verir, biz hüküm vermeyelim
        tg(f"🟡 FLAG DENETİMİ: son {WINDOW_DAYS} günde yalnız {n_win} sinyal var — denetim atlandı (Master Scan seyrek).")
        return 0

    for col in cols:
        n_fill_win  = cur.execute(f"SELECT COUNT({col}) FROM scan_signals WHERE scan_date>=?", (cut_win,)).fetchone()[0]
        n_fill_base = cur.execute(f"SELECT COUNT({col}) FROM scan_signals WHERE scan_date>=? AND scan_date<?", (cut_base, cut_win)).fetchone()[0]
        fill_pct = 100.0 * n_fill_win / n_win

        if col in KNOWN_DEAD:
            if n_fill_win > 0:
                revived.append(f"👻 {col}: bilinen-ölü kolon yeniden dolmaya başladı ({n_fill_win} satır) — kasıtlı mı?")
            lines.append(f"| `{col}` | %{fill_pct:.0f} | — | bilinen ölü |")
            continue

        # 1) YENİ ÖLDÜ
        if n_fill_win == 0:
            if n_fill_base > 0:
                problems.append(f"🔴 YENİ ÖLDÜ — {col}: önceki {BASELINE_DAYS-WINDOW_DAYS} günde {n_fill_base} satır doluydu, son {WINDOW_DAYS} günde SIFIR")
                lines.append(f"| `{col}` | %0 | — | 🔴 YENİ ÖLDÜ |")
            else:
                lines.append(f"| `{col}` | %0 | — | boş (eskiden de boştu) |")
            continue

        vals = cur.execute(
            f"SELECT {col}, COUNT(*) FROM scan_signals WHERE scan_date>=? AND {col} IS NOT NULL GROUP BY {col} ORDER BY 2 DESC",
            (cut_win,)).fetchall()
        n_distinct = len(vals)
        durum = "ok"

        if col in KNOWN_QUIRK:
            lines.append(f"| `{col}` | %{fill_pct:.0f} | {n_distinct} | bilinen suskun/gürültü (Eylül'de karar) |")
            continue

        # 2) TEK DEĞER (yeterli örnekte hiç ayrım yok)
        if n_distinct == 1 and n_fill_win >= MIN_N:
            problems.append(f"🟠 TEK DEĞER — {col}: {n_fill_win} satırın hepsi '{vals[0][0]}' (ölçmüyor)")
            durum = "🟠 TEK DEĞER"

        # 3) GÜRÜLTÜ (ikili flag'in yarıdan fazlası ateşliyor)
        val_set = {str(v[0]) for v in vals}
        if val_set <= {"0", "1", "0.0", "1.0"} and n_fill_win >= 300:
            fire = sum(c for v, c in vals if str(v) in ("1", "1.0"))
            fire_pct = 100.0 * fire / n_fill_win
            if fire_pct > BLOAT_PCT:
                problems.append(f"🟠 GÜRÜLTÜ — {col}: sinyallerin %{fire_pct:.0f}'i ateşliyor (ayrım gücü yok)")
                durum = f"🟠 GÜRÜLTÜ %{fire_pct:.0f}"

        lines.append(f"| `{col}` | %{fill_pct:.0f} | {n_distinct} | {durum} |")

    # 4) ÇELİŞKİ çiftleri
    for a, b in CONTRA_PAIRS:
        if a not in cols or b not in cols:
            continue
        both = cur.execute(
            f"SELECT SUM(CASE WHEN {a}=1 AND {b}=1 THEN 1 ELSE 0 END), COUNT(*) "
            f"FROM scan_signals WHERE scan_date>=? AND {a} IS NOT NULL AND {b} IS NOT NULL",
            (cut_win,)).fetchone()
        n_both, n_pair = (both[0] or 0), (both[1] or 0)
        if n_pair >= MIN_N and 100.0 * n_both / n_pair > CONTRA_PCT:
            problems.append(f"🔴 ÇELİŞKİ — {a} + {b}: {n_pair} satırın %{100.0*n_both/n_pair:.0f}'inde İKİSİ BİRDEN 1 (hesap bozuk)")

    con.close()
    problems += revived

    # Rapor dosyası
    os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
    rp = os.path.join(BASE, "logs", "flag_health_report.md")
    with open(rp, "w", encoding="utf-8") as f:
        f.write(f"# Flag Sağlık Raporu — {today} (pencere: son {WINDOW_DAYS} gün, n={n_win})\n\n")
        if problems:
            f.write("## SORUNLAR\n\n" + "\n".join(f"- {p}" for p in problems) + "\n\n")
        else:
            f.write("## Sorun yok ✅\n\n")
        f.write("| Kolon | Doluluk | Farklı değer | Durum |\n|---|--:|--:|---|\n")
        f.write("\n".join(lines) + "\n")
    print(f"Rapor yazıldı: {rp}")

    # Telegram
    if problems:
        msg = f"🚨 FLAG DENETİMİ ({today}) — {len(problems)} sorun:\n\n" + "\n".join(problems[:12])
        if len(problems) > 12:
            msg += f"\n… +{len(problems)-12} daha (logs/flag_health_report.md)"
    else:
        msg = f"✅ FLAG DENETİMİ ({today}): {len(cols)} kolon tarandı (n={n_win}), sorun yok."
    tg(msg)
    print(msg)
    return 1 if any(p.startswith("🔴") for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
