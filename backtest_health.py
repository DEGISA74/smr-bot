# -*- coding: utf-8 -*-
"""
backtest_health.py — TÜM BACKTEST/ANALİZ PY'LERİNİN SAĞLIK KONTROLÜ
===================================================================
Amaç: "backtest yapan tüm py'ler sorunsuz çalışmalı" (kullanıcı, 2 Tem 2026).
Her betiği (1) SYNTAX kontrol eder, (2) güvenli/hızlı olanları SMOKE-RUN edip
erken-crash (import hatası, UnicodeEncodeError, eksik kolon, şema kayması) yakalar.

Sınıflandırma:
  ✅ OK          — smoke-run 0 exit ile bitti (tam çalıştı)
  🟢 SYNTAX-OK   — sadece syntax (ağır/uzun betik; smoke-run edilmez)
  🟡 ÇALIŞIYOR   — smoke timeout doldu ama erken hata YOK (yavaş betik, sağlıklı)
  🔴 HATA        — çöktü (syntax VEYA runtime) → hemen düzelt

Çalıştır:  python backtest_health.py
"""
import sys, io, ast, subprocess, os
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SMOKE_TIMEOUT = 25   # sn — erken crash'i yakalamaya yeter; yavaş betik timeout=sağlıklı

# (dosya, smoke_calistir, arg) — ağır/uzun/DB-yazan/zamana-bağlı olanlar smoke-run EDİLMEZ
SCRIPTS = [
    ("backtest_runner.py",            False, []),          # 3.5dk + signal_results yazar → gece cron
    ("confluence_backtest.py",        True,  []),
    ("fingerprint_backtest.py",       True,  []),
    ("feature_karne.py",              True,  []),
    ("scanner_karne.py",              True,  ["--bugscan"]),
    ("universe_snapshot.py",          True,  []),
    ("backtest_poc_retest.py",        True,  []),
    ("backtest_ema_ribbon_reclaim.py",True,  []),
    ("firsat_backtest.py",            True,  []),
    ("ema_stop_backtest.py",          True,  []),
    ("vade_sweep.py",                 True,  []),
    ("vade_sweep_all.py",             True,  []),
    ("tavan_backtest.py",             True,  []),
    ("settle_report.py",              True,  []),
    ("flag_health_audit.py",          True,  ["--dry"]),   # --dry: Telegram'a GÖNDERMEZ
    ("panel_verdict_backtest.py",     True,  ["--sample", "10", "--days", "30"]),  # hızlı örneklem (~2sn)
    ("genel_ozet_verdict_backtest.py",True,  ["--sample", "10"]),  # GENEL ÖZET 6-oy verdicti (13 Tem 2026)
    ("rsi_kova_backtest.py",          True,  ["--sample", "30", "--days", "250"]),  # RSI uç rozet karnesi (17 Tem 2026, reform 2b)
    ("sert_gun_backtest.py",          True,  ["--sample", "30", "--days", "250"]),  # şok günü karnesi (17 Tem 2026, terazi şok paketi)
    ("golden_record.py",              False, []),          # ~1dk (app defs + 110 ölçüm) → elle: her hesap değişikliği sonrası
    ("app_boot_test.py",              False, []),          # ağır (tam açılış) → elle: her deploy öncesi

    ("settle_probe.py",               False, []),          # 21:00'e kadar poller → ASLA smoke-run
]

def check(fname, smoke, args):
    if not os.path.exists(fname):
        return "🔴 HATA", "dosya yok"
    # 1) SYNTAX
    try:
        ast.parse(open(fname, encoding="utf-8").read())
    except SyntaxError as e:
        return "🔴 HATA", f"syntax: satır {e.lineno}: {e.msg}"
    if not smoke:
        return "🟢 SYNTAX-OK", "ağır/uzun → smoke atlandı"
    # 2) SMOKE-RUN (erken crash avı)
    try:
        r = subprocess.run([sys.executable, fname, *args],
                           capture_output=True, text=True, timeout=SMOKE_TIMEOUT,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return "🟡 ÇALIŞIYOR", f"{SMOKE_TIMEOUT}sn timeout, erken hata yok"
    if r.returncode == 0:
        return "✅ OK", "tam çalıştı"
    tail = (r.stderr or r.stdout or "").strip().splitlines()
    return "🔴 HATA", (tail[-1] if tail else f"exit {r.returncode}")[:160]

def main():
    print("=== BACKTEST PY SAĞLIK KONTROLÜ ===")
    bad = 0
    for fname, smoke, args in SCRIPTS:
        status, note = check(fname, smoke, args)
        if status.startswith("🔴"):
            bad += 1
        print(f"{status:14s} {fname:32s} {note}")
    print("=" * 60)
    print("🔴 HATALI: %d / %d" % (bad, len(SCRIPTS)))
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
