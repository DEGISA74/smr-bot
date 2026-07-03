"""
TAVAN CANLI TAKİP — forward hit-rate + "DÜNKÜ ADAYLAR NE YAPTI" retrospektifi
============================================================================
Her gün üretilen TAVAN ADAYLARI listesini kalıcı olarak kaydeder; ertesi gün
gerçek sonucu ölçer. Böylece backtest'ten BAĞIMSIZ, canlı (forward) isabet oranı
izlenir — model çürümesi (drift) erken yakalanır.

İki çıktı:
  1. retrospective_text(date) → ilk ekrandaki "DÜNKÜ TAVAN ADAYLARI — NE YAPTI?"
     metnini üretir (gün-içi EN YÜKSEĞE göre, önceki kapanış → gün-içi zirve).
  2. live_hitrate() → son N günün gerçekleşmiş canlı isabet oranı (tavan/büyük).

Depolama: tavan_takip.json (VPS state, gitignore'da). Fiyat: veriler/*.parquet.

VPS entegrasyonu (günlük cron — Telegram-post scripti):
    import tavan_tracker as tt, tavan_scanner as ts
    tt.evaluate_pending()                       # dünküleri ölç
    retro = tt.retrospective_text(dun)          # "DÜNKÜ NE YAPTI" postu
    df,_,_,_ = ts.scan()                        # bugünkü liste
    tt.record_daily(df, bugun)                  # bugünküleri kaydet

CLI:
  python tavan_tracker.py record [--date YYYY-MM-DD]
  python tavan_tracker.py evaluate
  python tavan_tracker.py retro   [--date YYYY-MM-DD]
  python tavan_tracker.py hitrate [--days 60]
"""
import pandas as pd
import os, json, argparse
import tavan_engine as te

VERILER = 'veriler'
STORE = 'tavan_takip.json'


# ───────────────────── Depolama ─────────────────────
def _load():
    if not os.path.exists(STORE):
        return []
    try:
        with open(STORE, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return []


def _save(records):
    with open(STORE, 'w', encoding='utf-8') as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)


def _read_parquet(symbol):
    ct = symbol if symbol.endswith('.IS') else f'{symbol}.IS'
    fp = os.path.join(VERILER, f'{ct}_1d.parquet')
    if not os.path.exists(fp):
        return None
    try:
        return pd.read_parquet(fp).sort_index()
    except Exception:
        return None


# ───────────────────── Kayıt ─────────────────────
def record_daily(df_scored, signal_date, alarm_esik=te.ALARM_ESIK, top_n=7):
    """Bir günün adaylarını kaydeder. Öncelik: Skor ≥ alarm_esik (ALARM listesi);
    hiç yoksa TOP-N. Aynı (gün, sembol) tekrar yazılmaz."""
    signal_date = str(pd.Timestamp(signal_date).date())
    records = _load()
    seen = {(r['signal_date'], r['symbol']) for r in records}

    if df_scored is None or df_scored.empty:
        return []
    alarm = df_scored[df_scored['skor'] >= alarm_esik]
    picks = alarm if len(alarm) else df_scored.head(top_n)

    added = []
    for rank, (_, row) in enumerate(picks.iterrows(), start=1):
        sym = row['tk']
        if (signal_date, sym) in seen:
            continue
        rec = {
            'signal_date': signal_date,
            'symbol': sym,
            'skor': float(row['skor']),
            'kat': row['kat'],
            'entry': float(row['fiyat']),
            'alarm': bool(row['skor'] >= alarm_esik),
            'rank': rank,
            'next_date': None,
            'next_high_pct': None,
            'next_close_pct': None,
            'tavan': None,
            'buyuk': None,
            'evaluated': False,
        }
        records.append(rec)
        added.append(rec)
    _save(records)
    return added


# ───────────────────── Ölçüm ─────────────────────
def _eval_one(rec):
    """Kayda ait ertesi gün sonucunu hesapla. Döndürür: güncellendi mi?"""
    df = _read_parquet(rec['symbol'])
    if df is None or df.empty:
        return False
    sig_ts = pd.Timestamp(rec['signal_date'])
    pos = int(df.index.searchsorted(sig_ts))
    if pos >= len(df) or df.index[pos].date() != sig_ts.date():
        # sinyal günü veride yoksa hizala (en yakın <=)
        prior = df.index[df.index <= sig_ts]
        if len(prior) == 0:
            return False
        pos = int(df.index.get_loc(prior[-1]))
    nxt = pos + 1
    if nxt >= len(df):
        return False   # ertesi gün henüz yok — bekle
    entry = rec['entry'] or float(df.iloc[pos]['Close'])
    if entry <= 0:
        return False
    nc = float(df.iloc[nxt]['Close'])
    nh = float(df.iloc[nxt]['High'])
    rec['next_date'] = str(df.index[nxt].date())
    rec['next_close_pct'] = round((nc / entry - 1) * 100, 2)
    rec['next_high_pct'] = round((nh / entry - 1) * 100, 2)
    rec['tavan'] = rec['next_close_pct'] >= te.TAVAN_ESIK
    rec['buyuk'] = rec['next_close_pct'] >= te.BUYUK_HAREKET_ESIK
    rec['evaluated'] = True
    return True


def evaluate_pending():
    """Ölçülmemiş kayıtlardan ertesi günü hazır olanları ölçer. Ölçülen sayısını döner."""
    records = _load()
    n = 0
    for rec in records:
        if rec.get('evaluated'):
            continue
        if _eval_one(rec):
            n += 1
    _save(records)
    return n


# ───────────────────── Retrospektif ("DÜNKÜ NE YAPTI") ─────────────────────
def retrospective_text(signal_date):
    """İlk ekrandaki gibi 'DÜNKÜ TAVAN ADAYLARI — NE YAPTI?' metni.
    Gün-içi EN YÜKSEĞE göre (önceki kapanış → gün-içi zirve)."""
    signal_date = str(pd.Timestamp(signal_date).date())
    recs = [r for r in _load() if r['signal_date'] == signal_date and r.get('evaluated')]
    if not recs:
        return None
    recs.sort(key=lambda r: (r['next_high_pct'] if r['next_high_pct'] is not None else -999), reverse=True)
    nd = recs[0].get('next_date', '?')
    lines = [f"📊 DÜNKÜ TAVAN ADAYLARI — NE YAPTI?",
             f"{signal_date} seansı · gün içi EN YÜKSEĞE göre (önceki kapanış → gün-içi zirve)\n"]
    for r in recs:
        h = r['next_high_pct']
        lines.append(f"  {r['symbol']:<6} {h:+.1f}%   {r['entry']:.2f} → {r['entry']*(1+h/100):.2f}")
    highs = [r['next_high_pct'] for r in recs]
    pos_n = sum(1 for h in highs if h > 0)
    best = recs[0]
    lines.append("")
    lines.append(f"🏆 En iyi: {best['symbol']} {best['next_high_pct']:+.1f}%  ·  "
                 f"Ortalama {sum(highs)/len(highs):+.1f}%  ·  {len(recs)} adaydan {pos_n} tanesi artıda")
    lines.append(f"ℹ️ Gün-içi zirveye göre — kapanışı yansıtmaz. Geçmiş performans, "
                 f"gelecek getiri vaadi değildir.")
    return "\n".join(lines)


# ───────────────────── Canlı isabet (drift monitörü) ─────────────────────
def live_hitrate(window_days=60):
    """Son N günün gerçekleşmiş canlı isabet oranı. Backtest'ten bağımsız."""
    recs = [r for r in _load() if r.get('evaluated')]
    if not recs:
        return None
    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=window_days)
    recs = [r for r in recs if pd.Timestamp(r['signal_date']) >= cutoff]
    if not recs:
        return None
    alarm = [r for r in recs if r.get('alarm')]

    def _agg(rows):
        if not rows:
            return {'n': 0}
        n = len(rows)
        tav = sum(1 for r in rows if r['tavan'])
        buy = sum(1 for r in rows if r['buyuk'])
        avg_close = sum(r['next_close_pct'] for r in rows) / n
        avg_high = sum(r['next_high_pct'] for r in rows) / n
        return {
            'n': n,
            'tavan_hit': tav, 'tavan_pct': round(100 * tav / n, 1),
            'buyuk_hit': buy, 'buyuk_pct': round(100 * buy / n, 1),
            'avg_close_pct': round(avg_close, 2), 'avg_high_pct': round(avg_high, 2),
        }

    return {'window_days': window_days, 'tum': _agg(recs), 'alarm': _agg(alarm)}


# ───────────────────── CLI ─────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    p_rec = sub.add_parser('record'); p_rec.add_argument('--date', default=None)
    sub.add_parser('evaluate')
    p_ret = sub.add_parser('retro'); p_ret.add_argument('--date', default=None)
    p_hr = sub.add_parser('hitrate'); p_hr.add_argument('--days', type=int, default=60)
    args = ap.parse_args()

    if args.cmd == 'record':
        import tavan_scanner as ts
        df, target, rejim, chg = ts.scan(args.date)
        if target is None:
            print('Veri yok.'); return
        added = record_daily(df, target)
        print(f'{target.date()} · rejim {rejim} · {len(added)} aday kaydedildi.')
        for r in added:
            print(f"  {r['symbol']:<6} skor {r['skor']:.0f} {r['kat']} "
                  f"{'🚨ALARM' if r['alarm'] else ''}")

    elif args.cmd == 'evaluate':
        n = evaluate_pending()
        print(f'{n} kayıt ölçüldü.')

    elif args.cmd == 'retro':
        date = args.date
        if date is None:
            # en son ölçülmüş sinyal günü
            ev = [r['signal_date'] for r in _load() if r.get('evaluated')]
            date = max(ev) if ev else None
        txt = retrospective_text(date) if date else None
        print(txt or 'Ölçülmüş retrospektif yok.')

    elif args.cmd == 'hitrate':
        hr = live_hitrate(args.days)
        if not hr:
            print('Henüz ölçülmüş canlı veri yok.'); return
        print(f"=== CANLI İSABET (son {hr['window_days']} gün) ===")
        for label, key in [('TÜM adaylar', 'tum'), ('ALARM (skor≥150)', 'alarm')]:
            a = hr[key]
            if a['n'] == 0:
                print(f'{label}: veri yok'); continue
            print(f"{label}: n={a['n']} · tavan %{a['tavan_pct']} ({a['tavan_hit']}) · "
                  f"büyük %{a['buyuk_pct']} ({a['buyuk_hit']}) · "
                  f"ort kapanış {a['avg_close_pct']:+.2f}% · ort zirve {a['avg_high_pct']:+.2f}%")


if __name__ == '__main__':
    main()
