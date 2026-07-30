# -*- coding: utf-8 -*-
"""
golden_record.py — ALTIN KAYIT SİSTEMİ (4 Tem 2026, bölme öncesi emniyet kemeri)
================================================================================
Amaç: app.py'deki hesap fonksiyonlarının bugünkü davranışını "fotoğrafla",
sonraki her değişiklikte aynı donmuş veriyle aynı sonucu verdiğini doğrula.
Bir rakam bile değişirse alarm → "bozdum mu?" sorusu 30 saniyede cevaplanır.

Nasıl çalışır:
  1. streamlit sahte (stub) modülle değiştirilir — ekran hiç başlamaz.
  2. app.py AST ile okunur; SADECE tanımlar (def/class/import/güvenli sabitler)
     yüklenir. Üst-düzey UI kodu ve yan-etkili çağrılar ATLANIR.
  3. golden_fixtures/ içindeki DONMUŞ parquet'lerle ~20 hesap fonksiyonu koşulur.
  4. Sonuçlar golden_record.json ile karşılaştırılır.

Kullanım:
  python golden_record.py --init     # ilk fotoğrafı çek (veya bilinçli güncelle)
  python golden_record.py            # karşılaştır (fark varsa exit 1 + rapor)

⚠️ --init SADECE davranış değişikliği BİLEREK yapıldıysa koşulur.
   Refactor/taşıma sonrası asla --init ile "fark"ı susturma — farkı açıkla.
"""
import ast, json, math, os, sys, types

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE     = os.path.dirname(os.path.abspath(__file__))
APP_PY   = os.path.join(BASE, "app.py")
FIX_DIR  = os.path.join(BASE, "golden_fixtures")
GOLDEN   = os.path.join(BASE, "golden_record.json")
TICKERS  = ["TUPRS.IS", "SISE.IS", "ASTOR.IS", "SAHOL.IS", "EREGL.IS"]
BENCH    = "XU100.IS"

# ────────────────────────────────────────────────────────────────────────────
# 1) STREAMLIT STUB — ekranı hiç başlatmadan app.py tanımlarını yüklemek için
# ────────────────────────────────────────────────────────────────────────────
class _Stub:
    """Her attribute erişimi ve çağrı güvenli no-op. Decorator olarak
    kullanılırsa (@st.cache_data) fonksiyonu OLDUĞU GİBİ geri verir."""
    def __init__(self, name="st"):
        object.__setattr__(self, "_name", name)
    def __call__(self, *a, **k):
        if len(a) == 1 and callable(a[0]) and not k:
            f = a[0]
            try: f.clear = lambda *x, **y: None
            except Exception: pass
            return f
        return _Stub(self._name + "()")
    def __getattr__(self, n): return _Stub(f"{self._name}.{n}")
    def __setattr__(self, n, v): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __iter__(self): return iter([])
    def __bool__(self): return False
    def __getitem__(self, k): return _Stub(f"{self._name}[]")
    def __setitem__(self, k, v): pass
    def __contains__(self, k): return False
    def __repr__(self): return f"<Stub {self._name}>"


class _StrictSessionState(dict):
    """Terazi karakterizasyonunda sessiz eksik-kanıtı engelleyen katı oturum.

    Normal _Stub bilinmeyen her anahtarı falsy döndürür; bu davranış bir Terazi
    girdisi tohumlanmayı unutulduğunda oyun sessizce kaybolmasına yol açar.
    Burada her anahtar (kasıtlı boş olanlar dahil) fixture'da açıkça tanımlanmak
    zorundadır. Hedef fonksiyon hatayı kendi içinde yutsa bile unknown_reads
    koşu sonunda testi kırar.
    """
    def __init__(self, seed):
        super().__init__(seed)
        self.read_keys = []
        self.unknown_reads = []

    def _read(self, key):
        self.read_keys.append(key)
        if key not in self:
            self.unknown_reads.append(key)
            raise KeyError(f"Terazi fixture'ında session_state anahtarı tanımsız: {key}")
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        return self._read(key)

    def __getitem__(self, key):
        return self._read(key)


def _install_stubs():
    st_mod = types.ModuleType("streamlit")
    _s = _Stub("st")
    st_mod.__getattr__ = lambda n: getattr(_s, n)
    sys.modules["streamlit"] = st_mod
    ar = types.ModuleType("streamlit_autorefresh")
    ar.st_autorefresh = lambda *a, **k: 0
    sys.modules["streamlit_autorefresh"] = ar


# ────────────────────────────────────────────────────────────────────────────
# 2) AST FİLTRELİ YÜKLEME — sadece tanımlar, yan etki yok
# ────────────────────────────────────────────────────────────────────────────
_SAFE_CALLS = {
    "re.compile", "os.path.join", "os.path.dirname", "os.path.abspath",
    "os.getenv", "os.environ.get", "pytz.timezone", "dict", "list", "tuple",
    "set", "frozenset", "str", "int", "float", "range", "Path", "timedelta",
    "matplotlib.use",
}

def _dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None

def _expr_ok(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if (_dotted(sub.func) or "?") not in _SAFE_CALLS:
                return False
        elif isinstance(sub, (ast.Await, ast.Yield, ast.YieldFrom)):
            return False
    return True

def _includable(stmt):
    if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef,
                         ast.AsyncFunctionDef, ast.ClassDef)):
        return True
    if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
        return stmt.value is not None and _expr_ok(stmt.value)
    if isinstance(stmt, (ast.Try, ast.If)):
        parts = []
        for attr in ("body", "orelse", "finalbody"):
            parts += getattr(stmt, attr, []) or []
        for h in getattr(stmt, "handlers", []) or []:
            parts += h.body
        return all(_includable(s) for s in parts)
    return False

def load_app_defs(verbose=False):
    _install_stubs()
    src = open(APP_PY, encoding="utf-8").read()
    tree = ast.parse(src)
    kept = [s for s in tree.body if _includable(s)]
    ns = {"__name__": "app_defs", "__file__": APP_PY}
    skipped = []
    # Satır satır yükle: stub ile uyumsuz tek tük atama (örn. THEMES[st.session_state.x])
    # tüm yüklemeyi düşürmesin — o satır atlanır, fonksiyon tanımları etkilenmez.
    for stmt in kept:
        mod = ast.Module(body=[stmt], type_ignores=[])
        try:
            exec(compile(mod, "app.py(defs-only)", "exec"), ns)
        except Exception as e:
            skipped.append((stmt.lineno, type(e).__name__, str(e)[:80]))
    if skipped and verbose:
        print(f"  ({len(skipped)} üst-düzey satır atlandı — stub uyumsuz, tanımlar etkilenmez)")
        for ln, et, msg in skipped[:10]:
            print(f"   satır {ln}: {et}: {msg}")
    # Profil bloğu (_PROFILE_ENABLED/_tlog/_Timer, app.py ~126-155) AST filtresinde
    # os.makedirs Expr-statement'ı yüzünden ATLANIR → _Timer tanımsız kalırdı.
    # SMR_PROFILE kapalıyken üretimdeki davranış = no-op → burada no-op enjekte et
    # (böylece _Timer kullanan motorlar — PA-DNA — gerçek yolu koşar, None dönmez).
    if "_Timer" not in ns:
        class _NoTimer:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
        ns["_Timer"] = _NoTimer
        ns["_tlog"] = lambda *a, **k: None
        ns.setdefault("_PROFILE_ENABLED", False)
    # 16 Tem 2026 — BÖLME SONRASI MODÜL GERİ-DÜŞÜŞÜ:
    # Prob'lar (ns["calculate_supertrend"] gibi) eskiden app.py'nin from-import'ları
    # sayesinde ns'e düşüyordu — app.py o adları KULLANMASA bile. Ölü-import
    # temizliğinde o satırlar silinince 30 prob adı bulamadı (TİP list→str).
    # Motorlar artık modüllerde yaşıyor → eksik adları kaynağından doldur.
    # setdefault: app.py'nin KENDİ tanımları önceliklidir → mevcut ölçümler birebir korunur.
    import importlib as _il
    for _modname in ("indicators", "scanners", "ict_core", "scoring_core",
                     "scan_pipeline", "charts", "analysis_core", "data_layer",
                     "db_layer", "evidence", "pattern_core"):
        try:
            _m = _il.import_module(_modname)
        except Exception:
            continue
        for _k, _v in vars(_m).items():
            if not _k.startswith("__"):
                ns.setdefault(_k, _v)
    return ns


# ────────────────────────────────────────────────────────────────────────────
# 3) HEDEF FONKSİYONLAR — (ad, çağırıcı). df = hisse, bench = XU100 fixture
# ────────────────────────────────────────────────────────────────────────────
# Adım 7 (ict_core.py motor göçü) — ticker tabanlı motorlar veriyi İÇERİDE
# get_safe_historical_data ile çeker. Donmuş fotoğraf için fetcher'ı geçici olarak
# donmuş fixture döndürecek şekilde yamalıyoruz (hem taşıma öncesi ns, hem taşıma
# sonrası ict_core namespace'i — hangisinde yaşıyorsa). Sonuç iki durumda da AYNI
# donmuş df'ten üretilir → davranış-koruyan taşıma sıfır fark verir.
def _install_frozen_fetch(ns, df, bench):
    import sys as _s
    def _frozen(t, period=None, *a, **k):
        tu = str(t).upper()
        if tu.startswith("XU100") or tu.startswith("^") or "GSPC" in tu:
            return bench.copy()
        return df.copy()
    dicts = [ns]
    for _modname in ("ict_core", "scoring_core", "scan_pipeline", "charts", "analysis_core"):   # motorların yaşayabileceği modüller
        _m = _s.modules.get(_modname)
        if _m is not None:
            dicts.append(_m.__dict__)
    saved = []
    for d in dicts:
        saved.append((d, "get_safe_historical_data" in d, d.get("get_safe_historical_data")))
        d["get_safe_historical_data"] = _frozen
    return saved

def _restore_fetch(saved):
    for d, had, val in saved:
        if had:
            d["get_safe_historical_data"] = val
        else:
            d.pop("get_safe_historical_data", None)

def _strip_net(r):
    """PA-DNA'nın network-canlılığına + SEANS-SAATİNE bağlı alanlarını çıkar (golden determinizmi).
    vol_data_missing: canlı hacim-tazeleme fetch'i (GLDN.IS için fast_info/yf.download)
    başarılı mı diye bakar → network durumuna göre False/True oynar, hesap değil.
    vol_projected/vol_progress (İş 4, 28 Tem 2026): son barın gün-içi TAHMİN mi olduğu
    — seans açık/kapalıya göre değişir (wall-clock), hesap değil → golden'dan çıkar."""
    try:
        if isinstance(r, dict) and isinstance(r.get("smart_volume"), dict):
            r["smart_volume"].pop("vol_data_missing", None)
            r["smart_volume"].pop("vol_projected", None)
            r["smart_volume"].pop("vol_progress", None)
    except Exception:
        pass
    return r


def _tp(ns, name, df, bench, args=(), kwargs=None):
    """Ticker tabanlı motor probu: fetcher'ı dondur, cache'i temizle, çağır, geri al."""
    fn = ns[name]
    try: fn.clear()
    except Exception: pass
    saved = _install_frozen_fetch(ns, df, bench)
    try:
        return fn(*args, **(kwargs or {}))
    finally:
        _restore_fetch(saved)


# Adım 7c (scan_pipeline.py) — genel yamalayıcı: verilen isimleri ns + motor
# modüllerinin namespace'lerinde geçici değiştirir (taşıma öncesi/sonrası aynı).
def _install_patches(ns, mapping):
    import sys as _s
    dicts = [ns]
    for _modname in ("ict_core", "scoring_core", "scan_pipeline", "charts", "analysis_core"):
        _m = _s.modules.get(_modname)
        if _m is not None:
            dicts.append(_m.__dict__)
    saved = []
    for d in dicts:
        for k, v in mapping.items():
            saved.append((d, k, k in d, d.get(k)))
            d[k] = v
    return saved

def _restore_patches(saved):
    for d, k, had, val in saved:
        if had: d[k] = val
        else: d.pop(k, None)

def _tp7c(ns, name, df, bench, args=(), kwargs=None):
    """Boru hattı probu: tekli + toplu fetch + benchmark + MKK hepsi donmuş."""
    import pandas as _pd
    mi = _pd.concat({"GLDN.IS": df.copy()}, axis=1)   # MultiIndex (sembol, alan)
    def _frozen(t, period=None, *a, **k):
        tu = str(t).upper()
        if tu.startswith("XU100") or tu.startswith("^") or "GSPC" in tu:
            return bench.copy()
        return df.copy()
    mapping = {
        "get_safe_historical_data": _frozen,
        "get_batch_data_cached": lambda assets, period="1y", *a, **k: mi.copy(),
        "get_benchmark_data": lambda *a, **k: bench["Close"].copy() if hasattr(bench, "columns") else bench.copy(),
        "_compute_mkk_yabanci_signals": lambda t: {},
    }
    fn = ns[name]
    try: fn.clear()
    except Exception: pass
    saved = _install_patches(ns, mapping)
    try:
        return fn(*args, **(kwargs or {}))
    finally:
        _restore_patches(saved)

TARGETS = [
    ("compute_cmf_20g",        lambda ns, df, b: ns["compute_cmf"](df, period=20)),
    ("compute_cmf_5g",         lambda ns, df, b: ns["compute_cmf"](df, period=5)),
    ("compute_mfi_14",         lambda ns, df, b: ns["compute_mfi"](df, period=14)),
    ("compute_mfi_5",          lambda ns, df, b: ns["compute_mfi"](df, period=5)),
    ("force_index_dual",       lambda ns, df, b: ns["compute_force_index_dual"](df)),
    ("udvr_20g",               lambda ns, df, b: ns["compute_updown_volume_ratio"](df)),
    ("relative_obv_state",     lambda ns, df, b: ns["compute_relative_obv_state"](df, b)),
    ("harsi",                  lambda ns, df, b: ns["calculate_harsi"](df)),
    ("z_score_live",           lambda ns, df, b: ns["calculate_z_score_live"](df)),
    ("supertrend",             lambda ns, df, b: ns["calculate_supertrend"](df)),
    ("fib_levels",             lambda ns, df, b: ns["calculate_fib_levels"](df)),
    ("volume_profile_poc",     lambda ns, df, b: ns["calculate_volume_profile_poc"](df)),
    ("multi_tf_pocs",          lambda ns, df, b: ns["calculate_multi_tf_pocs"](df)),
    ("naked_poc",              lambda ns, df, b: ns["detect_naked_poc"](df)),
    ("volume_delta",           lambda ns, df, b: ns["calculate_volume_delta"](df)),
    ("lazybear_squeeze",       lambda ns, df, b: ns["check_lazybear_squeeze"](df)),
    ("market_regime",          lambda ns, df, b: ns["detect_market_regime"](df)),
    ("classic_candles",        lambda ns, df, b: ns["detect_classic_candle_patterns"](df)),
    ("harmonik_52h_strength",  lambda ns, df, b: ns["_harmonik_52h_strength"](df)),
    ("smart_sr_levels",        lambda ns, df, b: ns["find_smart_sr_levels"](df)),
    ("supply_demand_zones",    lambda ns, df, b: ns["detect_supply_demand_zones"](df)),
    ("er_build_context",       lambda ns, df, b: ns["_er_build_context"](df, b)),
    # Adım 4 (indicators.py) ek kapsama — 4 Tem 2026
    ("lazybear_breakout",      lambda ns, df, b: ns["check_lazybear_squeeze_breakout"](df)),
    ("darvas_box",             lambda ns, df, b: ns["detect_darvas_box"](df)),
    ("full_volume_profile",    lambda ns, df, b: ns["calculate_full_volume_profile"](df)),
    ("anchored_vwap_52h_top",  lambda ns, df, b: ns["calculate_anchored_vwap"](df, int(df["Close"].values.argmax()))),
    ("z_score_details",        lambda ns, df, b: ns["_z_score_details"](df)),
    ("spike_dom_probe",        lambda ns, df, b: [ns["_spike_dom_ratio"](a, c) for a, c in
                                                  ((5.0, 8.0), (-3.0, 8.0), (0.7, 1.0), (0.0, 0.0))]),
    # Adım 6a (scanners.py) kapsama — 6 Tem 2026: tek-hisse tarayıcı çekirdekleri
    ("sc_accumulation",        lambda ns, df, b: ns["process_single_accumulation"]("GLDN.IS", df, b["Close"])),
    ("sc_radar1",              lambda ns, df, b: ns["process_single_radar1"]("GLDN.IS", df, b["Close"])),
    ("sc_radar2",              lambda ns, df, b: ns["process_single_radar2"]("GLDN.IS", df, b, 0, 999999, 0)),
    ("sc_ict_setup",           lambda ns, df, b: ns["process_single_ict_setup"]("GLDN.IS", df)),
    ("sc_guclu_donus",         lambda ns, df, b: ns["calculate_guclu_donus_adaylari"]("GLDN.IS", df, b["Close"])),
    ("sc_prelaunch_bos",       lambda ns, df, b: ns["calculate_prelaunch_bos"]("GLDN.IS", df, b["Close"])),
    ("sc_nadir_firsat",        lambda ns, df, b: ns["_nadir_firsat_single_fast"]("GLDN.IS", df, b["Close"])),
    # Adım 6b (Erken Radar ailesi) kapsama — 7 Tem 2026
    ("er_evaluate",            lambda ns, df, b: ns["evaluate_erken_radar"]("GLDN.IS", df, b)),
    ("er_kisa_aciklama_probe", lambda ns, df, b: {k: ns["_er_kisa_aciklama"](k) for k in ("A1", "B8", "C2", "D1")}),
    # Adım 7 (ict_core.py motor göçü) kapsama — 9 Tem 2026: ICT deep + PA-DNA +
    # Minervini SEPA + Harmonik küme + PA-with-context (davranış-koruyan taşıma).
    ("pa_with_context",        lambda ns, df, b: ns["detect_price_action_with_context"](df.copy())),
    ("ict_deep",               lambda ns, df, b: _tp(ns, "calculate_ict_deep_analysis", df, b, args=("GLDN.IS",))),
    ("pa_dna",                 lambda ns, df, b: _strip_net(_tp(ns, "calculate_price_action_dna", df, b, args=("GLDN.IS",)))),
    ("minervini_sepa",         lambda ns, df, b: _tp(ns, "calculate_minervini_sepa", df, b, args=("GLDN.IS",), kwargs={"provided_df": df.copy()})),
    ("harmonic_patterns",      lambda ns, df, b: _tp(ns, "calculate_harmonic_patterns", df, b, args=("GLDN.IS", df.copy()))),
    ("harmonic_confluence",    lambda ns, df, b: _tp(ns, "calculate_harmonic_confluence", df, b, args=("GLDN.IS",), kwargs={"df": df.copy()})),
    # Adım 7b (scoring_core.py) kapsama — 9 saf skorlama motoru (davranış-koruyan taşıma).
    ("smart_money_score",      lambda ns, df, b: _tp(ns, "calculate_smart_money_score", df, b, args=("GLDN.IS",))),
    ("sentiment_score",        lambda ns, df, b: _tp(ns, "calculate_sentiment_score", df, b, args=("GLDN.IS",))),
    ("master_score",           lambda ns, df, b: _tp(ns, "calculate_master_score", df, b, args=("GLDN.IS",), kwargs={"return_breakdown": True})),
    ("risk_profile",           lambda ns, df, b: _tp(ns, "_compute_risk_profile", df, b, args=("GLDN.IS",))),
    ("tech_card_data",         lambda ns, df, b: _tp(ns, "get_tech_card_data", df, b, args=("GLDN.IS",))),
    ("liquidity_manip",        lambda ns, df, b: ns["_liquidity_manip"](df.copy())),
    ("smc_elements",           lambda ns, df, b: ns["_compute_smc_elements"](df["High"].values, df["Low"].values, df["Open"].values, df["Close"].values, 5)),
    ("breakout_state",         lambda ns, df, b: ns["_detect_breakout_state"](df.copy(), float(df["High"].tail(20).max()))),
    ("split_scores",           lambda ns, df, b: ns["compute_smart_money_split_scores"](_SPLIT_PROBE_DICT)),
    # Adım 7c (scan_pipeline.py) kapsama — boru hattının ağır hesapları (davranış-koruyan taşıma).
    ("pipe_signal_features",   lambda ns, df, b: _tp7c(ns, "_compute_signal_features", df, b, args=("GLDN.IS",))),
    ("pipe_chart_patterns",    lambda ns, df, b: _tp7c(ns, "scan_chart_patterns", df, b, args=(["GLDN.IS"],))),
    ("pipe_golden_agent",      lambda ns, df, b: _tp7c(ns, "scan_golden_pattern_agent", df, b, args=(["GLDN.IS"],))),
    # Adım 8 (charts.py) kapsama — ana fiyat grafiği YAPISAL parmak izi (trace tip/ad/uzunluk +
    # shape/annotation sayıları + SMC özeti). Görsel doğrulama gözle; bu prob yapıyı korur.
    ("price_chart_struct",     lambda ns, df, b: _chart_fingerprint(_tp(ns, "_main_price_chart_plotly", df, b, args=("GLDN.IS", True)))),
    # Adım 9a (analysis_core.py) kapsama — panel + AI prompt besleyen analiz motorları.
    # NOT: _compute_volume_quality_label golden'a EKLENMEZ — çıktısı finalize_volume.py
    # marker dosyasına bağlı (FINAL/FINAL_ISYATIRIM), dış-durum+zaman bağımlı, parmak izi olamaz.
    ("an_risk_profile",        lambda ns, df, b: _tp7c(ns, "_risk_profile", df, b, args=("GLDN.IS",))),
    ("an_scanner_strength",    lambda ns, df, b: _tp(ns, "_scanner_setup_strength", df, b, args=("GLDN.IS",))),
    ("an_scanner_tiers",       lambda ns, df, b: _tp(ns, "get_active_scanner_tiers", df, b, args=("GLDN.IS",))),
    ("an_er_prompt_text",      lambda ns, df, b: ns["build_erken_radar_prompt_text"](ns["evaluate_erken_radar"]("GLDN.IS", df.copy(), b.copy()))),
    ("an_roadmap",             lambda ns, df, b: _tp7c(ns, "calculate_8_point_roadmap", df, b, args=("GLDN.IS",), kwargs={"cat": "BIST 500"})),
    ("an_mtf_alignment",       lambda ns, df, b: _tp(ns, "calculate_multi_timeframe_alignment", df, b, args=("GLDN.IS",))),
    ("an_synthetic_sentiment", lambda ns, df, b: _tp(ns, "calculate_synthetic_sentiment", df, b, args=("GLDN.IS",))),
    ("an_weekly_frame",        lambda ns, df, b: _tp(ns, "compute_weekly_frame", df, b, args=("GLDN.IS",))),
    ("an_ict_reversal",        lambda ns, df, b: ns["detect_ict_reversal"](df.copy())),
    ("an_advanced_levels",     lambda ns, df, b: _tp(ns, "get_advanced_levels_data", df, b, args=("GLDN.IS",))),
    ("an_obv_divergence",      lambda ns, df, b: _tp(ns, "get_obv_divergence_status", df, b, args=("GLDN.IS",))),
    ("an_breakout",            lambda ns, df, b: ns["process_single_breakout"]("GLDN.IS", df.copy())),
    ("an_stp",                 lambda ns, df, b: ns["process_single_stock_stp"]("GLDN.IS", df.copy())),
]

def _chart_fingerprint(ret):
    """(fig, smc_summary) → deterministik yapısal özet (uid'siz)."""
    try:
        fig, smc = ret
    except Exception:
        return {"__hata__": str(ret)[:120]}
    if isinstance(fig, str):
        return {"__hata__": fig[:120], "smc": _ser(smc)}
    traces = []
    for tr in fig.data:
        try:
            _x = getattr(tr, "x", None)
            _n = len(_x) if _x is not None else 0
        except Exception:
            _n = -1
        traces.append([tr.type, getattr(tr, "name", None), _n])
    lay = fig.layout
    return {
        "traces": traces,
        "n_shapes": len(lay.shapes) if lay.shapes else 0,
        "n_annotations": len(lay.annotations) if lay.annotations else 0,
        "smc": _ser(smc),
    }

# 7b split_scores probu için sabit temsili feature_dict (deterministik → taşıma öncesi/sonrası aynı)
_SPLIT_PROBE_DICT = {
    'f_yabanci_giris': 1, 'f_yabanci_streak': 3, 'f_yabanci_cikis': 0,
    'f_cmf_dual': 1, 'f_rel_obv_state': 'outperform_strong', 'f_poc_confluence': 1,
    'f_poc_magnet': 1, 'f_at_vwap_minus_2sigma': 1, 'f_breaker_block_active': 0,
    'f_near_ifvg': 0, 'f_cum_delta_dual': 1, 'f_mfi_dual': 1, 'f_rsi_dual': 1,
    'f_rsi_mfi_bouquet': 1, 'f_udvr_state': 'accumulation', 'f_udvr_climax': 1,
    'f_breakout_state': 'confirmed',
}


# ────────────────────────────────────────────────────────────────────────────
# 4) GENEL SERİLEŞTİRİCİ — her dönüş tipini deterministik JSON'a çevir
# ────────────────────────────────────────────────────────────────────────────
def _ser(v, depth=0):
    import numpy as np
    import pandas as pd
    if depth > 8: return "<derinlik-limiti>"
    if v is None or isinstance(v, (bool, str)): return v
    if isinstance(v, (int,)) or isinstance(v, np.integer): return int(v)
    if isinstance(v, float) or isinstance(v, np.floating):
        f = float(v)
        return "NaN" if math.isnan(f) else ("Inf" if math.isinf(f) else round(f, 6))
    if isinstance(v, (pd.Timestamp,)): return str(v)
    if isinstance(v, pd.Series):
        tail = [_ser(x, depth + 1) for x in v.tail(5).tolist()]
        return {"__series__": True, "len": int(len(v)), "tail5": tail}
    if isinstance(v, pd.DataFrame):
        last = {str(c): _ser(v[c].iloc[-1], depth + 1) for c in v.columns} if len(v) else {}
        return {"__df__": True, "shape": list(v.shape), "cols": [str(c) for c in v.columns], "last_row": last}
    if isinstance(v, dict):
        return {str(k): _ser(x, depth + 1) for k, x in sorted(v.items(), key=lambda p: str(p[0]))}
    if isinstance(v, (list, tuple, set)):
        seq = sorted(v, key=str) if isinstance(v, set) else list(v)
        if len(seq) > 40: seq = seq[:40] + [f"<+{len(seq)-40} öğe>"]
        return [_ser(x, depth + 1) for x in seq]
    if isinstance(v, np.ndarray):
        return _ser(v.tolist(), depth + 1)
    if callable(v):
        # bellek adresi her süreçte değişir → sadece ad kaydet (davranışı zaten
        # bu fonksiyonları ÇAĞIRAN problar ölçer, örn. er_evaluate)
        return f"<callable {getattr(v, '__name__', '?')}>"
    return f"<{type(v).__name__}> {str(v)[:120]}"


# Modül-düzeyi tablolar/sabitler — taşıma (bölme) sırasında birebir korunmalı.
# Eski elle girilmiş ER/Güç puan tabloları 29 Tem 2026'da kaldırıldı.
GLOBAL_TARGETS = [
    "SCANNER_TIER_MAP", "ER_ELIT_SCORE_MIN", "SCANNER_PLAIN_DESC",
    "ERKEN_RADAR_SCENARIOS",   # Adım 6b — senaryo sözlüğü birebir korunmalı
]

def _globals_snapshot(ns):
    rec = {g: _ser(ns.get(g, "__MISSING__")) for g in GLOBAL_TARGETS}
    # saf yardımcılar — sabit girdilerle davranış fotoğrafı
    try:
        rec["_guc_plain_probe"] = {g: _ser(ns["_guc_plain"](g)) for g in ("🟢", "🟡", "🔴")}
    except Exception as e:
        rec["_guc_plain_probe"] = f"__ERROR__ {e}"
    return rec


def _db_probe(ns):
    """Adım 5 (db_layer) davranış fotoğrafı — GEÇİCİ test veritabanında.
    Gerçek patron.db'ye ASLA dokunmaz. Tarihler bugüne göreli üretilir →
    her çalıştırmada aynı sınıflandırma (deterministik)."""
    import sqlite3, tempfile, datetime as dt, sys as _sys
    rec = {}
    tmp = os.path.join(tempfile.gettempdir(), "golden_dbprobe.db")
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        return {"__skip__": "temp db silinemedi"}
    # DB_FILE'ı geçici dosyaya yönlendir (taşıma öncesi: ns; sonrası: db_layer modülü)
    _m = _sys.modules.get("db_layer")
    if _m is not None and hasattr(_m, "DB_FILE"):
        _m.DB_FILE = tmp
    ns["DB_FILE"] = tmp
    try:
        ns["init_db"]()
        con = sqlite3.connect(tmp)
        rec["schema"] = sorted(" ".join(r[0].split())
                               for r in con.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        # watchlist akışı
        ns["add_watchlist_db"]("GOLDEN.IS")
        rec["watchlist_add"] = _ser(ns["load_watchlist_db"]())
        ns["remove_watchlist_db"]("GOLDEN.IS")
        rec["watchlist_remove"] = _ser(ns["load_watchlist_db"]())
        # MKK sentetik: dün 'in' + 4 günlük streak → sinyaller deterministik
        _y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        con.execute("INSERT INTO mkk_yabanci VALUES (?,?,?,?,?)", (_y, "GLDN", "in", 55.0, None))
        con.execute("INSERT INTO mkk_yabanci VALUES (?,?,?,?,?)", (_y, "GLDN", "streak", None, 4))
        con.execute("INSERT INTO mkk_yabanci VALUES (?,?,?,?,?)", (_y, "CIKN", "out", -40.0, None))
        con.commit()
        rec["mkk_giris_streak"] = _ser(ns["_compute_mkk_yabanci_signals"]("GLDN.IS"))
        rec["mkk_cikis"] = _ser(ns["_compute_mkk_yabanci_signals"]("CIKN.IS"))
        rec["mkk_bos"] = _ser(ns["_compute_mkk_yabanci_signals"]("YOKX.IS"))
        # senaryo yaşı: 3 gün üst üste er_A1 → yaş 3 beklenir
        _t = dt.date.today()
        for _i in (1, 2, 3):
            con.execute("INSERT INTO scan_signals (scan_date, symbol, scan_type) VALUES (?,?,?)",
                        ((_t - dt.timedelta(days=_i)).isoformat(), "GLDN", "er_A1"))
        con.commit(); con.close()
        rec["scenario_age"] = _ser(ns["get_scenario_ages_batch"]([("GLDN.IS", "A1")]))
    except Exception as e:
        rec["__error__"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        # gerçek yola GERİ döndür (başka probe'lar yanlış DB'ye gitmesin)
        if _m is not None and hasattr(_m, "DB_FILE"):
            _m.DB_FILE = "patron.db"
        ns["DB_FILE"] = "patron.db"
    return rec


def _pipeline_probe(ns):
    """Adım 7c: log_scan_signal davranış fotoğrafı — GEÇİCİ DB'de tek satır yaz-oku.
    Alias-tolerant kolon eşleme + feature fallback yolu ölçülür. Tarih kolonları
    OKUNMAZ (her gün değişir → sahte fark üretirdi)."""
    import sqlite3, tempfile, sys as _sys
    import pandas as _pd
    rec = {}
    tmp = os.path.join(tempfile.gettempdir(), "golden_pipeprobe.db")
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except Exception:
        return {"__skip__": "temp db silinemedi"}
    _dbm = _sys.modules.get("db_layer")
    if _dbm is not None and hasattr(_dbm, "DB_FILE"):
        _dbm.DB_FILE = tmp
    saved = _install_patches(ns, {
        "DB_FILE": tmp,
        "_compute_signal_features": lambda t: {"f_rsi": 55.5, "f_52h_pos": 0.42, "f_master_score": 61.0},
    })
    try:
        ns["init_db"]()
        df_res = _pd.DataFrame([{"Sembol": "GLDN.IS", "Skor": 77, "Fiyat": 123.45,
                                 "F_RSI": 48.2, "F_52H_Pos": 0.66, "F_Master": 58.0}])
        ns["log_scan_signal"]("golden_probe", df_res, category="BIST 500")
        con = sqlite3.connect(tmp)
        rows = con.execute("SELECT symbol, scan_type, score, entry_price, category, "
                           "f_rsi, f_52h_pos, f_master_score FROM scan_signals ORDER BY id").fetchall()
        con.close()
        rec["written"] = _ser([list(r) for r in rows])
    except Exception as e:
        rec["__error__"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        _restore_patches(saved)
        if _dbm is not None and hasattr(_dbm, "DB_FILE"):
            _dbm.DB_FILE = "patron.db"
    return rec


def _terazi_fingerprint(out):
    """Render metinlerinden bağımsız, karar davranışını tam yakalayan özet."""
    out = out if isinstance(out, dict) else {}
    ter = out.get("terazi") if isinstance(out.get("terazi"), dict) else {}

    def _vote(v):
        if not isinstance(v, dict):
            return {"__hata__": str(v)[:120]}
        return {
            "ad": v.get("ad"),
            "yon": v.get("yon"),
            "agirlik": _ser(v.get("agirlik")),
            "olculmus": bool(v.get("olculmus")),
            "prio": _ser(v.get("prio")),
            "family": v.get("family"),
            "semsiye": bool(v.get("semsiye", False)),
        }

    return {
        "skor": _ser(out.get("skor")),
        "label": out.get("label"),
        "n_scanner": _ser(out.get("n_scanner")),
        "best": out.get("best"),
        "guven": _ser(out.get("guven")),
        "celiski": bool(out.get("celiski")),
        "boga": _ser(out.get("boga")),
        "ayi": _ser(out.get("ayi")),
        "tek_sinyal": bool(out.get("tek_sinyal")),
        "terazi": {
            "yon": ter.get("yon"),
            "guven": _ser(ter.get("guven")),
            "celiski": bool(ter.get("celiski")),
            "tek_sinyal": bool(ter.get("tek_sinyal")),
            "boga_w": _ser(ter.get("boga_w")),
            "ayi_w": _ser(ter.get("ayi_w")),
            "n_votes": _ser(ter.get("n_votes")),
            "endeks_notu": ter.get("endeks_notu"),
            "sistemik": ter.get("sistemik"),
            "sok_serit": ter.get("sok_serit"),
            "karsi": _vote(ter.get("karsi")) if ter.get("karsi") else None,
            "votes": [_vote(v) for v in (ter.get("votes") or [])],
        },
    }


def _terazi_session(er_rows=None, formasyon=None, formasyon_ticker=""):
    """Her senaryo için taze ve eksiksiz session_state tohumu."""
    import pandas as _pd
    er_cols = ["Sembol", "ScenarioId", "Role", "Guc"]
    er = _pd.DataFrame(er_rows or [], columns=er_cols)
    return {
        "erken_radar_data": er,
        "prelaunch_bos_data": _pd.DataFrame(columns=["Sembol"]),
        "accum_data": _pd.DataFrame(columns=["Sembol"]),
        "_er_single_cache": {},
        "_formasyon_chart_data": formasyon,
        "_formasyon_ticker": formasyon_ticker,
        "bist_market_status": {},
    }


def _terazi_cases():
    """0a sözleşmesi: dış dünya sabit, Terazi çekirdeği ve oy motorları gerçektir."""
    primary = {"Sembol": "GLDN.IS", "ScenarioId": "A1",
               "Role": "primary", "Guc": "güçlü"}
    red_d4 = {"Sembol": "GLDN.IS", "ScenarioId": "D4",
              "Role": "red_flag", "Guc": "zayıf"}
    red_d5 = {"Sembol": "GLDN.IS", "ScenarioId": "D5",
              "Role": "red_flag", "Guc": "zayıf"}
    cap_rows = [
        {"Sembol": f"{sym}.IS", "ScenarioId": "A1",
         "Role": "primary", "Guc": "güçlü"}
        for sym in ("AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "ZZZZ")
    ]
    normal_breadth = (45.0, -0.2, 500)

    return {
        "guclu_boga": {
            "ticker": "GLDN.IS",
            "session": _terazi_session([primary]),
            "scanner_scores": {"er_A1": {"score": 90}},
            "features": {
                "f_rsi": 20.0, "f_cmf_dual": "strong_pos",
                "f_yabanci_streak": 3, "f_sfp_bull": 1,
            },
            "pack": {
                "_sig_hacim": 1, "_sig_obv": 1,
                "_sig_yapi": 1, "_sig_mfi": 1,
            },
            "breadth": normal_breadth,
            "frs": None,
            "expect": {
                "yon": "yukari",
                "required_votes": {
                    "Pozitif karneli tarama ailesi",
                    "RSI uç aşırı satım",
                    "Para akışı güçlü pozitif",
                    "Yabancı 3+ gün alıcı",
                    "Bullish SFP (ayı tuzağı)",
                    "Yapı yükseliyor",
                },
            },
        },
        "celiski": {
            "ticker": "GLDN.IS",
            "session": _terazi_session(
                [primary, red_d4],
                formasyon={"type": "double_top"},
                formasyon_ticker="GLDN.IS",
            ),
            "scanner_scores": {"er_A1": {"score": 90}},
            "features": {
                "f_rsi": 22.0, "f_cmf_dual": "strong_pos",
                "f_sfp_bear": 1,
            },
            "pack": {
                "_sig_hacim": 0, "_sig_obv": 0,
                "_sig_yapi": 1, "_sig_mfi": 0,
            },
            "breadth": normal_breadth,
            "frs": {"kutu": "kirdi", "durum": "fail"},
            "expect": {
                "celiski": True,
                "required_votes": {
                    "Pozitif karneli tarama ailesi",
                    "Şemsiye: kurumsal satış izi",
                    "Ayı formasyonu",
                    "Başarısız kırılım",
                    "Bearish SFP (boğa tuzağı)",
                },
            },
        },
        "ayi_formasyonu_ve_bayrak": {
            "ticker": "GLDN.IS",
            "session": _terazi_session(
                [red_d4, red_d5],
                formasyon={"type": "double_top"},
                formasyon_ticker="GLDN.IS",
            ),
            "scanner_scores": {},
            "features": {
                "f_rsi": 50.0, "f_cmf_dual": "strong_neg",
                "f_sfp_bear": 1,
            },
            "pack": {
                "_sig_hacim": -1, "_sig_obv": -1,
                "_sig_yapi": -1, "_sig_mfi": -1,
            },
            "breadth": normal_breadth,
            "frs": None,
            "expect": {
                "yon": "asagi",
                "required_votes": {
                    "Şemsiye: kurumsal satış izi",
                    "Şemsiye: trend bozuldu",
                    "Ayı formasyonu",
                    "Para akışı güçlü negatif",
                    "Bearish SFP (boğa tuzağı)",
                    "Yapı bozuluyor",
                },
            },
        },
        "sistemik_gun": {
            "ticker": "GLDN.IS",
            "session": _terazi_session([primary]),
            "scanner_scores": {"er_A1": {"score": 90}},
            "features": {"f_rsi": 20.0},
            "pack": {
                "_sig_hacim": 0, "_sig_obv": 0,
                "_sig_yapi": 0, "_sig_mfi": 0,
            },
            "breadth": (92.0, -4.1, 500),
            "frs": None,
            "expect": {
                "yon": "yukari",
                "sistemik": True,
                "required_votes": {
                    "Pozitif karneli tarama ailesi",
                    "RSI uç aşırı satım",
                },
            },
        },
        "cap5_bilinen_kusur_6_aday": {
            "ticker": "ZZZZ.IS",
            "session": _terazi_session(cap_rows),
            "scanner_scores": {"er_A1": {"score": 90}},
            "features": {},
            "pack": {
                "_sig_hacim": 0, "_sig_obv": 0,
                "_sig_yapi": 0, "_sig_mfi": 0,
            },
            "breadth": normal_breadth,
            "frs": None,
            "expect": {
                "skor": 90,
                "n_scanner": 1,
                "required_votes": {"Pozitif karneli tarama ailesi"},
                "goldmine_raw_symbols": [
                    "AAAA", "BBBB", "CCCC", "DDDD", "EEEE", "ZZZZ",
                ],
                "goldmine_capped_symbols": [
                    "AAAA", "BBBB", "CCCC", "DDDD", "EEEE",
                ],
            },
        },
    }


def _run_terazi_case(ns, case_name, spec, stock_df, bench_df):
    """Değiştirilmemiş üretim toplayıcısını katı, donmuş girdilerle çalıştır."""
    st_mod = ns["st"]
    had_session = "session_state" in st_mod.__dict__
    old_session = st_mod.__dict__.get("session_state")
    strict_state = _StrictSessionState(spec["session"])
    st_mod.session_state = strict_state

    def _frozen(t, period=None, *a, **k):
        tu = str(t).upper()
        if tu.startswith(("XU100", "^")) or "GSPC" in tu:
            return bench_df.copy()
        return stock_df.copy()

    mapping = {
        "get_safe_historical_data": _frozen,
        "get_scanner_scores": lambda: dict(spec.get("scanner_scores") or {}),
        "_compute_signal_features": lambda _t: dict(spec.get("features") or {}),
        "compute_genel_ozet_pack": lambda _t, _status=None: dict(spec.get("pack") or {}),
        "_piyasa_genisligi_bugun": lambda: tuple(spec.get("breadth") or (None, None, 0)),
    }
    saved = _install_patches(ns, mapping)
    try:
        exp = spec.get("expect") or {}
        goldmine_raw = None
        goldmine_capped = None
        if ("goldmine_raw_symbols" in exp
                or "goldmine_capped_symbols" in exp):
            goldmine_raw = ns["_compute_goldmine_entries"]()
            goldmine_capped = ns["_cap_goldmine_entries"](goldmine_raw)
        result = ns["_compute_kanit_ozeti"](
            spec["ticker"], frs=spec.get("frs"))
        split_context = ns["_collect_kanit_context"](
            spec["ticker"], frs=spec.get("frs"))
        reads_before_build = len(strict_state.read_keys)
        split_result = ns["_build_kanit_ozeti"](split_context)
        if len(strict_state.read_keys) != reads_before_build:
            raise AssertionError(
                f"{case_name}: saf hüküm kurucu session_state okudu")
    finally:
        _restore_patches(saved)
        if had_session:
            st_mod.session_state = old_session
        else:
            st_mod.__dict__.pop("session_state", None)

    if strict_state.unknown_reads:
        raise AssertionError(
            f"{case_name}: tohumlanmamış session_state erişimi: "
            f"{sorted(set(strict_state.unknown_reads))}")

    fp = _terazi_fingerprint(result)
    split_fp = _terazi_fingerprint(split_result)
    if fp != split_fp:
        raise AssertionError(
            f"{case_name}: uçtan-uca sonuç ile ayrıştırılmış yol farklı")
    ter = fp["terazi"]
    votes = {v.get("ad") for v in ter.get("votes", [])}
    missing = set(exp.get("required_votes") or set()) - votes
    forbidden = set(exp.get("forbidden_votes") or set()) & votes
    if missing:
        raise AssertionError(f"{case_name}: beklenen oylar yok: {sorted(missing)}")
    if forbidden:
        raise AssertionError(f"{case_name}: yasak oylar oluştu: {sorted(forbidden)}")
    if "yon" in exp and ter.get("yon") != exp["yon"]:
        raise AssertionError(
            f"{case_name}: yön {ter.get('yon')!r}, beklenen {exp['yon']!r}")
    if "celiski" in exp and ter.get("celiski") is not exp["celiski"]:
        raise AssertionError(
            f"{case_name}: çelişki {ter.get('celiski')!r}, "
            f"beklenen {exp['celiski']!r}")
    if exp.get("sistemik") and not ter.get("sistemik"):
        raise AssertionError(f"{case_name}: sistemik gün şeridi oluşmadı")
    if "skor" in exp and fp.get("skor") != exp["skor"]:
        raise AssertionError(
            f"{case_name}: skor {fp.get('skor')!r}, beklenen {exp['skor']!r}")
    if "n_scanner" in exp and fp.get("n_scanner") != exp["n_scanner"]:
        raise AssertionError(
            f"{case_name}: tarama ailesi {fp.get('n_scanner')!r}, "
            f"beklenen {exp['n_scanner']!r}")
    if "goldmine_raw_symbols" in exp:
        raw_symbols = [str(e.get("sym")) for e in (goldmine_raw or [])]
        if raw_symbols != exp["goldmine_raw_symbols"]:
            raise AssertionError(
                f"{case_name}: ham Gold Mine sırası {raw_symbols!r}, "
                f"beklenen {exp['goldmine_raw_symbols']!r}")
    if "goldmine_capped_symbols" in exp:
        capped_symbols = [str(e.get("sym")) for e in (goldmine_capped or [])]
        if capped_symbols != exp["goldmine_capped_symbols"]:
            raise AssertionError(
                f"{case_name}: vitrin/log sırası {capped_symbols!r}, "
                f"beklenen {exp['goldmine_capped_symbols']!r}")

    rec = {
        "result": fp,
        "session_reads": sorted(set(strict_state.read_keys)),
    }
    if spec.get("known_defect"):
        rec["known_defect"] = spec["known_defect"]
    return rec


def _terazi_probe(ns, stock_df, bench_df):
    """0a/0b: 5 sözleşmeli senaryo ile mevcut Terazi davranış fotoğrafı."""
    # Korumanın kendisini de sına: bilinmeyen anahtar sessiz falsy olmamalı.
    guard = _StrictSessionState({})
    try:
        guard.get("__golden_missing_seed_probe__")
    except KeyError:
        pass
    else:
        raise AssertionError("Katı session_state bilinmeyen anahtarı durdurmadı")
    if guard.unknown_reads != ["__golden_missing_seed_probe__"]:
        raise AssertionError("Katı session_state bilinmeyen erişimi kaydetmedi")

    rec = {}
    for name, spec in _terazi_cases().items():
        rec[name] = _run_terazi_case(
            ns, name, spec, stock_df.copy(), bench_df.copy())
    return rec


def snapshot(ns):
    import pandas as pd
    bench = pd.read_parquet(os.path.join(FIX_DIR, f"{BENCH}_frozen.parquet"))
    out = {"__GLOBALS__": _globals_snapshot(ns), "__DB_LAYER__": _db_probe(ns),
           "__PIPELINE__": _pipeline_probe(ns)}
    for t in TICKERS:
        df = pd.read_parquet(os.path.join(FIX_DIR, f"{t}_frozen.parquet"))
        rec = {}
        for name, fn in TARGETS:
            try:
                rec[name] = _ser(fn(ns, df.copy(), bench.copy()))
            except Exception as e:
                rec[name] = f"__ERROR__ {type(e).__name__}: {str(e)[:160]}"
        out[t] = rec
    # 0a/0b (30 Tem 2026): değiştirilmemiş üretim Terazisi; dış dünya donmuş,
    # session_state katı, oy motorları gerçektir. Cap-5 mevcut kusur olarak korunur.
    stock = pd.read_parquet(os.path.join(FIX_DIR, f"{TICKERS[0]}_frozen.parquet"))
    out["__TERAZI__"] = _terazi_probe(ns, stock, bench)
    return out


# ────────────────────────────────────────────────────────────────────────────
# 5) KARŞILAŞTIRMA
# ────────────────────────────────────────────────────────────────────────────
def _diff(a, b, path, out):
    if type(a) is not type(b):
        out.append(f"{path}: TİP {type(a).__name__} → {type(b).__name__}")
    elif isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:   out.append(f"{path}.{k}: YENİ eklendi")
            elif k not in b: out.append(f"{path}.{k}: KAYBOLDU")
            else: _diff(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: uzunluk {len(a)} → {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _diff(x, y, f"{path}[{i}]", out)
    elif a != b:
        out.append(f"{path}: {a!r} → {b!r}")


def main():
    init_mode = "--init" in sys.argv
    terazi_only = "--terazi-only" in sys.argv
    print("app.py tanımları yükleniyor (ekransız)...")
    ns = load_app_defs(verbose=True)
    if terazi_only:
        import pandas as pd
        bench = pd.read_parquet(os.path.join(FIX_DIR, f"{BENCH}_frozen.parquet"))
        stock = pd.read_parquet(os.path.join(FIX_DIR, f"{TICKERS[0]}_frozen.parquet"))
        print("Terazi karakterizasyonu: 5 katı senaryo...")
        rec = _terazi_probe(ns, stock, bench)
        for name, item in rec.items():
            result = item["result"]
            ter = result["terazi"]
            print(f"  ✅ {name}: yön={ter['yon']} · boğa={ter['boga_w']} · "
                  f"ayı={ter['ayi_w']} · oy={ter['n_votes']} · "
                  f"çelişki={ter['celiski']}")
        print("✅ TERAZİ-ONLY: 5/5 senaryo sözleşmesi geçti.")
        return 0
    n_funcs = sum(1 for _, f in TARGETS)
    print(f"Fotoğraf çekiliyor: {len(TICKERS)} hisse × {n_funcs} fonksiyon "
          f"+ 5 Terazi senaryosu...")
    now = snapshot(ns)

    n_err = sum(1 for t in now.values() for v in t.values()
                if isinstance(v, str) and v.startswith("__ERROR__"))
    if init_mode:
        json.dump(now, open(GOLDEN, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, sort_keys=True)
        print(f"✅ Altın kayıt yazıldı: {GOLDEN}")
        if n_err:
            print(f"⚠️ {n_err} fonksiyon hata verdi (kayıtta __ERROR__ olarak duruyor):")
            for t, rec in now.items():
                for k, v in rec.items():
                    if isinstance(v, str) and v.startswith("__ERROR__"):
                        print(f"   {t} · {k}: {v[:120]}")
        return 0

    if not os.path.exists(GOLDEN):
        print("❌ Altın kayıt yok. Önce: python golden_record.py --init")
        return 2
    gold = json.load(open(GOLDEN, encoding="utf-8"))
    diffs = []
    _diff(gold, now, "", diffs)
    if diffs:
        print(f"🔴 FARK BULUNDU — {len(diffs)} nokta değişti:")
        for d in diffs[:40]:
            print("  " + d)
        if len(diffs) > 40:
            print(f"  ... +{len(diffs)-40} fark daha")
        print("\nBu fark BİLEREK mi yapıldı? Evet ise açıkla ve 'python golden_record.py --init' ile güncelle.")
        return 1
    print(f"✅ BİREBİR AYNI — {len(TICKERS)}×{n_funcs} ölçüm + 5 Terazi senaryosu, "
          f"sıfır fark. (hata kaydı: {n_err})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
