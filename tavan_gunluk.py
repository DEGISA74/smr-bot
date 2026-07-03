"""
TAVAN GÜNLÜK ORKESTRATÖR — VPS cron için tek giriş noktası
==========================================================
Günlük Telegram akışının tamamını tek çağrıda üretir:
  1. Dünkü adayları ölç (evaluate_pending)
  2. "DÜNKÜ TAVAN ADAYLARI — NE YAPTI?" retrospektif metni
  3. Bugünü tara (tavan_engine → tek kaynak) + kaydet (record_daily)
  4. "TAVAN ADAYLARI — bugün" mesaj metni (ALARM ≥150 / TOP 7)

Cron / systemd:  python tavan_gunluk.py            → iki mesajı stdout'a basar
Kod içinden:     retro, bugun = tavan_gunluk.daily()  # ikisi de str | None

Not: İsabet satırı CANLI forward veriden (tavan_tracker.live_hitrate) gelir —
yeterli örnek birikince otomatik dolar. Backtest headline'ı istersen BACKTEST_NOT
sabitini `python tavan_backtest.py` çıktısına göre elle günceller.
"""
import pandas as pd
import tavan_scanner as ts
import tavan_tracker as tt
import tavan_engine as te

TOP_N = 7
# Son `python tavan_backtest.py` özeti (elle güncellenir; boşsa satır atlanır).
BACKTEST_NOT = ""   # örn: "60g/1131 tavan · Skor ≥150 %11 · random×3.4"

_KAT_ISIM = {'A': 'Momentum', 'C': 'Sıkışma', 'E': 'Direnç', 'D': 'Dip Dönüş'}


def _hikaye(r):
    """Hisseye özel kısa hikâye (app.py _tav_hikaye ile aynı mantık)."""
    k = r['kat']
    if k == 'A':
        return f"Momentum — 10g %{r['Ret10g']:+.0f} · RSI {r['RSI']:.0f} · 52H zirvede"
    if k == 'C':
        return f"Sıkışma — bant darlığı {r['BBrank']:.0f} · 20g direncin %{r['NearH20']:.0f}'inde"
    if k == 'E':
        return f"Direnç — 20g zirvenin %{r['NearH20']:.0f}'inde · hacim {r['VolT']:.1f}x"
    if k == 'D':
        return f"Dip Dönüş — 52H'nin %{r['52H%']:.0f}'inde · RSI {r['RSI']:.0f} · sessiz"
    return ""


def build_today_message(df, target, rejim, chg):
    """İkinci ekrandaki 'TAVAN ADAYLARI' postunu üretir."""
    if df is None or df.empty:
        return None
    date_str = pd.Timestamp(target).strftime('%d.%m.%Y')
    alarm = df[df['skor'] >= te.ALARM_ESIK]
    picks = alarm if len(alarm) else df.head(TOP_N)

    lines = [f"🚀 TAVAN ADAYLARI — {date_str}"]
    # İsabet satırı — canlı forward veriden
    hr = tt.live_hitrate(60)
    if hr and hr['alarm']['n'] >= 10:
        a = hr['alarm']
        lines.append(f"Canlı isabet (ALARM, son 60g): tavan %{a['tavan_pct']} · "
                     f"büyük %{a['buyuk_pct']} (n={a['n']})")
    elif BACKTEST_NOT:
        lines.append(f"Backtest: {BACKTEST_NOT}")
    lines.append(f"Rejim: {rejim} · XU100 10g {chg:+.2f}%\n")

    if len(alarm):
        lines.append(f"🚨 ALARM (Skor ≥{te.ALARM_ESIK}) · {len(picks)} hisse, top {min(len(picks), TOP_N)}:")
    else:
        lines.append(f"🚨 ALARM yok — en yüksek skorlu top {TOP_N}:")

    for _, r in picks.head(TOP_N).iterrows():
        lines.append(f"  {r['tk']:<6} ({r['skor']:.0f})  {_hikaye(r)}")

    lines.append("")
    lines.append("ℹ️ Algoritmanın izlediği TAVAN adayları — kesinlik değil, olasılık. "
                 "Yatırım tavsiyesi değildir.")
    return "\n".join(lines)


def daily(record=True):
    """Tam günlük akış. Döner: (retrospektif_metni, bugun_metni) — ikisi de str|None."""
    # 1) Dünküleri ölç
    n_eval = tt.evaluate_pending()
    # 2) En son ölçülmüş günün retrospektifi
    ev_days = sorted({r['signal_date'] for r in tt._load() if r.get('evaluated')})
    retro = tt.retrospective_text(ev_days[-1]) if ev_days else None
    # 3) Bugünü tara
    df, target, rejim, chg = ts.scan()
    if target is None:
        return retro, None
    # 4) Kaydet + bugün mesajı
    if record:
        tt.record_daily(df, target)
    today = build_today_message(df, target, rejim, chg)
    return retro, today, {'evaluated': n_eval, 'target': str(pd.Timestamp(target).date())}


if __name__ == '__main__':
    out = daily()
    retro, today = out[0], out[1]
    meta = out[2] if len(out) > 2 else {}
    print(f"[tavan_gunluk] {meta.get('target','?')} · {meta.get('evaluated',0)} kayıt ölçüldü\n")
    print("=" * 60)
    print("MESAJ 1 — DÜNKÜ NE YAPTI (retrospektif)")
    print("=" * 60)
    print(retro or "(henüz ölçülmüş dünkü aday yok)")
    print("\n" + "=" * 60)
    print("MESAJ 2 — BUGÜNÜN TAVAN ADAYLARI")
    print("=" * 60)
    print(today or "(veri yok)")
