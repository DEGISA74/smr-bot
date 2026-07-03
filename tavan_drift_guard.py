"""
DRIFT-GUARD TESTİ — tavan_engine.py ⇔ app.py B38 gömülü kopyası
================================================================
Amaç: canlı app.py'deki _tav_* motoru ile tavan_engine.py'nin BİREBİR aynı skoru
verdiğini her koşuda garantilemek. Böylece 18 Haz'da olan "üç kopya sessizce
ayrıştı" hatası bir daha olamaz.

app.py bir Streamlit dosyası olduğu için import EDİLMEZ (import Streamlit'i başlatır).
Bunun yerine AST ile SADECE _tav_* fonksiyonları + _TAV_REJIM_AGIRLIK çıkarılır,
izole bir namespace'te exec edilir ve motorla sentetik veri üzerinde karşılaştırılır.

Çalıştırma:  python tavan_drift_guard.py   (PASS → exit 0, FAIL → exit 1)
             VPS deploy öncesi çalıştırılması önerilir (app.py ⇔ engine sapmasını yakalar).
Not: veriler/ parquet GEREKMEZ — tamamen sentetik OHLCV ile çalışır.
"""
import ast
import sys
import numpy as np
import pandas as pd

import tavan_engine as te

APP_PATH = 'app.py'
WANT_NAMES = {
    '_tav_rsi', '_tav_is_manipulated', '_tav_features',
    '_tav_score_A', '_tav_score_C', '_tav_score_E', '_tav_score_D',
    '_TAV_REJIM_AGIRLIK',
}


def extract_app_engine(path=APP_PATH):
    """app.py'den sadece _tav_* düğümlerini çıkarıp izole namespace'te exec et."""
    with open(path, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    picked = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef,)) and node.name in WANT_NAMES:
            picked.append(node)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in WANT_NAMES:
                    picked.append(node)
    mod = ast.Module(body=picked, type_ignores=[])
    ns = {'np': np, 'pd': pd}
    exec(compile(mod, path, 'exec'), ns)
    missing = WANT_NAMES - set(ns)
    if missing:
        raise AssertionError(f'app.py içinde bulunamayan motor parçaları: {missing}')
    return ns


def make_synthetic(seed, n=140):
    """Rastgele ama gerçekçi OHLCV — trend + gürültü + hacim dalgası."""
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0008, 0.001)
    rets = rng.normal(drift, 0.025, n)
    close = 10 * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0.01, 0.008, n)))
    low = close * (1 - np.abs(rng.normal(0.01, 0.008, n)))
    open_ = low + (high - low) * rng.random(n)
    vol = np.abs(rng.normal(1_000_000, 400_000, n)) * (1 + 0.5 * np.sin(np.arange(n) / 7))
    idx = pd.bdate_range('2025-01-01', periods=n)
    return pd.DataFrame({'Open': open_, 'High': high, 'Low': low,
                         'Close': close, 'Volume': vol}, index=idx)


def dict_close(a, b, tol=1e-9):
    if set(a) != set(b):
        return False, f'anahtar farkı: {set(a) ^ set(b)}'
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, (bool, np.bool_)) or isinstance(vb, (bool, np.bool_)):
            if bool(va) != bool(vb):
                return False, f'{k}: {va} != {vb}'
        elif va is None or vb is None:
            if va is not vb:
                return False, f'{k}: {va} != {vb}'
        else:
            fa, fb = float(va), float(vb)
            if np.isnan(fa) and np.isnan(fb):
                continue
            if not np.isclose(fa, fb, atol=tol, rtol=1e-9):
                return False, f'{k}: {fa} != {fb}'
    return True, ''


def main():
    app = extract_app_engine()
    fails = []

    # 1) Rejim ağırlıkları birebir mi?
    if te.REJIM_AGIRLIK != app['_TAV_REJIM_AGIRLIK']:
        fails.append('REJIM_AGIRLIK app.py ile eşleşmiyor (DRIFT!)')
    else:
        print('✓ REJIM_AGIRLIK app.py ile birebir')

    # 2) Confluence eşiği canlıyla (30) aynı mı? Kaynak metinde doğrula.
    with open(APP_PATH, encoding='utf-8') as fh:
        src = fh.read()
    if '(srt[1]-30)' not in src.replace(' ', ''):
        fails.append('app.py confluence eşiği 30 değil — CONF_THRESHOLD sapması')
    elif te.CONF_THRESHOLD != 30:
        fails.append(f'engine.CONF_THRESHOLD={te.CONF_THRESHOLD}, canlı 30 bekleniyor')
    else:
        print('✓ Confluence eşiği 30 (canlı ile aynı)')

    # 3) Fonksiyon bazlı birebir sayısal eşleşme — çok sayıda sentetik df × index
    checks = 0
    for seed in range(25):
        df = make_synthetic(seed)
        for i in (60, 80, 100, 120, len(df) - 1):
            # is_manipulated
            m_te = te.is_manipulated(df, i)
            m_app = app['_tav_is_manipulated'](df, i)
            if bool(m_te) != bool(m_app):
                fails.append(f'is_manipulated seed={seed} i={i}: {m_te} != {m_app}')
                continue
            # rsi (son değer)
            r_te = float(te.rsi(df['Close']).iloc[-1])
            r_app = float(app['_tav_rsi'](df['Close']).iloc[-1])
            if not (np.isnan(r_te) and np.isnan(r_app)) and not np.isclose(r_te, r_app, atol=1e-9):
                fails.append(f'rsi seed={seed}: {r_te} != {r_app}')
            # features
            f_te = te.features(df, i)
            f_app = app['_tav_features'](df, i)
            if (f_te is None) != (f_app is None):
                fails.append(f'features None uyuşmazlığı seed={seed} i={i}')
                continue
            if f_te is None:
                checks += 1
                continue
            ok, why = dict_close(f_te, f_app)
            if not ok:
                fails.append(f'features seed={seed} i={i}: {why}')
                continue
            # kalıp skorları
            for name, fn_te, fn_app in [
                ('A', te.score_A, app['_tav_score_A']),
                ('C', te.score_C, app['_tav_score_C']),
                ('E', te.score_E, app['_tav_score_E']),
                ('D', te.score_D, app['_tav_score_D']),
            ]:
                s_te = fn_te(f_te)
                s_app = fn_app(f_te)
                if not np.isclose(float(s_te), float(s_app), atol=1e-9):
                    fails.append(f'score_{name} seed={seed} i={i}: {s_te} != {s_app}')
            checks += 1
    print(f'✓ {checks} sentetik nokta test edildi (features + 4 kalıp skoru)')

    if fails:
        print(f'\n❌ FAIL — {len(fails)} sapma:')
        for msg in fails[:20]:
            print(f'   • {msg}')
        sys.exit(1)
    print('\n✅ PASS — tavan_engine.py, canlı app.py B38 motoruyla birebir.')
    sys.exit(0)


if __name__ == '__main__':
    main()
