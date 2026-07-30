# -*- coding: utf-8 -*-
"""
tarama_merkezi.py — TOPLU TARAMA KARAR MASASI (Aşama 4, 30 Tem 2026)
====================================================================
Amaç: Master Scan'in ürettiği `toplu_terazi_data` + tarama tablolarını okuyup
toplu tarama render'ını "araç listesi"nden KARAR MASASI'na çevirmek.

KESİN İLKELER (Codex + Claude mutabakatı):
  * Yeni hüküm motoru YOK. terazi_core sonuçları (toplu_terazi_data) tüketilir.
  * Yeni ağırlıklı skor / sabit yüzde YOK. SIRALI KAPI mantığı.
  * Sert risk ÖNCE veto eder (terazi'nin KENDİ sistemik/yön alanı — yeni eşik uydurulmaz).
  * Her hisse tek karar bölümüne düşer (tekilleştirme kapı sırasıyla).
  * Ölçülmemiş dönüş kurulumları (RSI Pozitif Uyumsuzluk) LONG'a yükseltilmez → Yeni Sinyaller.
  * Tek-hisse ekranına DOKUNULMAZ; kart tıklaması mevcut on_scan_result_click'e gider.

Bu modül SAF MANTIK (Streamlit'siz, test edilebilir) + RENDER (st) olarak ikiye ayrılır.
Karar sınıflandırması app.py/data'ya bağımlı değildir → test_tarama_merkezi.py sentetik
veriyle doğrular.
"""

# ── KARAR BÖLÜMLERİ ──────────────────────────────────────────────────────────
BUCKET_LONG = "oncelikli_long"
BUCKET_TEYIT = "teyit"
BUCKET_YENI = "yeni"
BUCKET_RISK = "risk"

BUCKET_LABELS = {
    BUCKET_LONG: "🚀 Öncelikli LONG",
    BUCKET_TEYIT: "⏳ Teyit Bekleyenler",
    BUCKET_YENI: "🌱 Yeni Sinyaller",
    BUCKET_RISK: "⚠️ Risk Masası",
}

# Yaşam döngüsü aşama sırası (liderlik_yolculugu_data · Liderlik_Asamasi)
_STAGE_ORDER = {
    "YENİ LİDER": 0,
    "LİDERLİK TEYİTLİ": 1,
    "ADAY": 2,
    "GEÇ SİNYAL": 3,
}
_LATE_STAGE = "GEÇ SİNYAL"

# Ölçülmemiş dönüş kaynağı (Öncelikli LONG'a giremez, Yeni'de "erken dönüş" alt bölümü)
_REVERSAL_SOURCES = {"rsi_pozitif_uyumsuzluk"}

# Katalog: (session_key, görünen ad, aile) — karar vitrini DEĞİL, denetim alanı.
CATALOG_MAP = [
    ("erken_radar_data", "Erken Radar", "Erken Kurulum"),
    ("guclu_donus_data", "Güçlü Dönüş", "Dönüş"),
    ("wilder_divergence_data", "RSI Pozitif Uyumsuzluk", "Dönüş"),
    ("minervini_data", "Minervini SEPA", "Trend & Liderlik"),
    ("rs_leaders_data", "RS Momentum Liderleri", "Trend & Liderlik"),
    ("liderlik_yolculugu_data", "Liderlik Yaşam Döngüsü", "Trend & Liderlik"),
    ("prelaunch_bos_data", "Pre-Launch BOS", "Kırılım"),
    ("golden_pattern_data", "Formasyon (V2)", "Kırılım & Formasyon"),
    ("accum_data", "Gizli Birikim", "Akıllı Para"),
    ("harmonic_confluence_data", "Harmonik Confluence", "Kesişim & Etiketler"),
    ("ict_scan_data", "ICT Setup", "Kesişim & Etiketler"),
    ("nadir_firsat_scan_data", "Nadir Fırsat", "Kesişim & Etiketler"),
    ("scan_data", "Radar (Market Intelligence)", "Radar"),
]
# Katalogda hisse sembolü hangi kolonlarda aranır (tarama df'leri farklı ad kullanır)
_SYMBOL_COLS = ("Sembol", "Hisse", "Sembol_Raw", "symbol", "Ticker")


# ── SAF YARDIMCILAR ──────────────────────────────────────────────────────────
def _clean_sym(s):
    return str(s or "").upper().replace(".IS", "").strip()


def _row_symbol(row):
    for _c in _SYMBOL_COLS:
        try:
            v = row.get(_c)
        except Exception:
            v = None
        if v:
            return _clean_sym(v)
    return ""


def _veto_reason(terazi):
    """Sert risk vetosu — YALNIZ terazi'nin KENDİ ölçülmüş hükmünü kullanır.
    Yeni eşik/skor uydurulmaz. Ayı oyu/çelişki tek başına veto DEĞİLDİR."""
    if not isinstance(terazi, dict):
        return None
    if terazi.get("sistemik"):
        return "Piyasa şoku — sistemik gün, hisse hükmü askıda"
    return None


def classify_candidate(item, lifecycle_stage=None):
    """Tek adayı kapı sırasıyla sınıflandırır. Döner: (bucket, gate_report dict).

    Kapı sırası (ağırlık YOK):
      0. Sert risk vetosu?          → RISK
      1. Karne sınıfı kabul mü?     (kanıtlı = ölçülmüş pozitif)
      2. Terazi yukarı destekli mi?
      3. Çelişki LONG'a izin mi?
      4. Sinyal yaşam evresi (geç değil mi)?
      5. Bağımsız kanıt (tek sinyale dayanmıyor mu)?
    Hepsi geçerse LONG. Karne ölçülmüş ama bir kapı eksikse TEYİT.
    Karne ölçülmemişse (dönüş/ölçümde) YENİ.
    """
    result = (item or {}).get("result") or {}
    terazi = result.get("terazi") or {}
    label = result.get("label", "kanıt yok")
    yon = terazi.get("yon", "dengede")
    celiski = bool(result.get("celiski") or terazi.get("celiski"))
    tek_sinyal = bool(result.get("tek_sinyal") or terazi.get("tek_sinyal"))
    n_scanner = int(result.get("n_scanner") or 0)
    sources = list((item or {}).get("sources") or [])
    is_reversal = any(s in _REVERSAL_SOURCES for s in sources)
    is_late = (lifecycle_stage == _LATE_STAGE)

    gates = {
        "veto": None,
        "karne_ok": (label == "kanıtlı"),
        "terazi_up": (yon == "yukari"),
        "no_celiski": (not celiski),
        "not_late": (not is_late),
        "bagimsiz_kanit": (not tek_sinyal),
    }
    report = {
        "label": label, "yon": yon, "celiski": celiski,
        "tek_sinyal": tek_sinyal, "n_scanner": n_scanner,
        "lifecycle": lifecycle_stage, "is_reversal": is_reversal,
        "sources": sources, "gates": gates, "missing": [], "veto_reason": None,
    }

    # 0) Sert risk vetosu — her şeyden önce
    _vr = _veto_reason(terazi)
    if _vr:
        gates["veto"] = _vr
        report["veto_reason"] = _vr
        return BUCKET_RISK, report

    # Karne ölçülmemişse (kanıt bekliyor / kanıt yok) → YENİ (LONG'a yükseltilmez)
    if label != "kanıtlı":
        report["yeni_alt"] = "erken_donus" if is_reversal else "olcumde"
        return BUCKET_YENI, report

    # Karne ölçülmüş (kanıtlı): kalan kapıları dene
    _missing = []
    if not gates["terazi_up"]:
        _missing.append("Terazi yukarı yönü henüz desteklemiyor")
    if not gates["no_celiski"]:
        _missing.append("Boğa–ayı çelişkisi var")
    if not gates["not_late"]:
        _missing.append("Sinyal yaşam döngüsünde geç kaldı")
    if not gates["bagimsiz_kanit"]:
        _missing.append("Tek kanıta dayanıyor (bağımsız teyit eksik)")
    report["missing"] = _missing

    if not _missing:
        return BUCKET_LONG, report
    return BUCKET_TEYIT, report


def _sort_key(cand):
    """Sahte yarış sırası YOK. Anahtar: karne sınıfı → yaşam evresi → yaş → sembol."""
    result = cand.get("item", {}).get("result") or {}
    skor = result.get("skor")
    skor = -int(skor) if isinstance(skor, (int, float)) else 0  # yüksek karne öne
    stage_ord = _STAGE_ORDER.get(cand.get("lifecycle_stage"), 5)
    yas = cand.get("lifecycle_yas")
    yas = int(yas) if isinstance(yas, (int, float)) else 999  # taze teyit öne (küçük yaş)
    return (skor, stage_ord, yas, cand.get("sym", ""))


def build_decision_desk(toplu_terazi_data, lifecycle_lookup=None):
    """toplu_terazi_data + yaşam döngüsü → 4 karar bölümü (tekilleştirilmiş, sıralı).

    lifecycle_lookup: {clean_sym: {'stage': str, 'yas': int}} (opsiyonel).
    Döner: {'oncelikli_long':[...], 'teyit':[...], 'yeni':[...], 'risk':[...],
            'as_of', 'status', 'counts', 'meta'}
    Her aday sözlüğü: {sym, ticker, item, lifecycle_stage, lifecycle_yas, bucket, report}
    Her hisse toplu_terazi_data.items'te bir kez → bölümler doğal olarak tekil.
    """
    lifecycle_lookup = lifecycle_lookup or {}
    out = {BUCKET_LONG: [], BUCKET_TEYIT: [], BUCKET_YENI: [], BUCKET_RISK: [],
           "as_of": None, "status": "not_ready", "counts": {}, "meta": {}}
    if not isinstance(toplu_terazi_data, dict):
        return out
    out["as_of"] = toplu_terazi_data.get("as_of")
    out["status"] = toplu_terazi_data.get("status", "not_ready")
    out["meta"] = {
        "category": toplu_terazi_data.get("category"),
        "candidate_count": toplu_terazi_data.get("candidate_count"),
        "ready_count": toplu_terazi_data.get("ready_count"),
        "error_count": toplu_terazi_data.get("error_count"),
        "batch_last_bar": toplu_terazi_data.get("batch_last_bar"),
    }
    items = toplu_terazi_data.get("items") or {}
    for _clean, _item in items.items():
        _lc = lifecycle_lookup.get(_clean) or {}
        _stage = _lc.get("stage")
        bucket, report = classify_candidate(_item, lifecycle_stage=_stage)
        cand = {
            "sym": _clean,
            "ticker": _item.get("ticker", _clean),
            "item": _item,
            "lifecycle_stage": _stage,
            "lifecycle_yas": _lc.get("yas"),
            "bucket": bucket,
            "report": report,
        }
        out[bucket].append(cand)
    for _b in (BUCKET_LONG, BUCKET_TEYIT, BUCKET_YENI, BUCKET_RISK):
        out[_b].sort(key=_sort_key)
    out["counts"] = {_b: len(out[_b]) for _b in
                     (BUCKET_LONG, BUCKET_TEYIT, BUCKET_YENI, BUCKET_RISK)}
    return out


def build_lifecycle_lookup(lifecycle_df):
    """liderlik_yolculugu_data DataFrame → {clean_sym: {'stage','yas'}}."""
    lookup = {}
    if lifecycle_df is None or not hasattr(lifecycle_df, "empty") or lifecycle_df.empty:
        return lookup
    try:
        for _, row in lifecycle_df.iterrows():
            _s = _row_symbol(row)
            if not _s:
                continue
            try:
                _yas = row.get("Liderlik_Yasi")
                _yas = int(_yas) if _yas is not None else None
            except Exception:
                _yas = None
            lookup[_s] = {"stage": row.get("Liderlik_Asamasi"), "yas": _yas}
    except Exception:
        pass
    return lookup


def build_catalog(session_getter):
    """Tarama tablolarını denetim kataloğuna çevirir (karar vitrini DEĞİL).
    session_getter: key -> DataFrame|None (st.session_state.get). Hisse tekrarı SERBEST.
    Döner: [{'key','name','family','count','symbols':[...]}]"""
    catalog = []
    for _key, _name, _family in CATALOG_MAP:
        _df = None
        try:
            _df = session_getter(_key)
        except Exception:
            _df = None
        if _df is None or not hasattr(_df, "empty") or _df.empty:
            catalog.append({"key": _key, "name": _name, "family": _family,
                            "count": 0, "symbols": []})
            continue
        _syms = []
        try:
            for _, row in _df.iterrows():
                _s = _row_symbol(row)
                if _s and _s not in _syms:
                    _syms.append(_s)
        except Exception:
            pass
        catalog.append({"key": _key, "name": _name, "family": _family,
                        "count": len(_syms), "symbols": _syms})
    return catalog


def candidate_scan_membership(clean_sym, catalog):
    """Bir hissenin katalogda çıktığı tüm tarama adları (kart 'diğer taramalar' için)."""
    return [c["name"] for c in (catalog or []) if clean_sym in c.get("symbols", [])]


# ── RENDER (Streamlit) ───────────────────────────────────────────────────────
# NOT: streamlit lazy import → saf mantık (yukarısı) test'te streamlit'siz koşar.
_YON_LABEL = {
    "yukari": ("YUKARI", "#22c55e"),
    "asagi": ("AŞAĞI", "#ef4444"),
    "dengede": ("DENGEDE", "#94a3b8"),
}


def _yon_chip(yon, celiski):
    lbl, col = _YON_LABEL.get(yon, ("—", "#94a3b8"))
    if celiski:
        return "ÇELİŞKİ", "#f59e0b"
    return lbl, col


def _reason_line(bucket, report):
    """Kartın 'neden bu bölümde' tek satırı (sahte 'en iyi işlem' iddiası YOK)."""
    if bucket == BUCKET_LONG:
        return "Bütün kapıları geçti"
    if bucket == BUCKET_RISK:
        return report.get("veto_reason") or "Sert risk vetosu"
    if bucket == BUCKET_YENI:
        if report.get("yeni_alt") == "erken_donus":
            return "Erken dönüş kurulumu — ölçümde (Öncelikli LONG değildir)"
        return "Yeni/ölçümde — karne henüz yeterli değil"
    _m = report.get("missing") or []
    return "Eksik kapı: " + (_m[0] if _m else "teyit tamamlanmadı")


def _render_card(st, cand, catalog, on_click, idx):
    _sym = cand["sym"]
    _report = cand["report"]
    _result = cand["item"].get("result") or {}
    _terazi = _result.get("terazi") or {}
    _yon_lbl, _yon_col = _yon_chip(_terazi.get("yon", "dengede"),
                                   bool(_result.get("celiski")))
    _best = _result.get("best") or (cand["item"].get("sources") or ["—"])[0]
    _skor = _result.get("skor")
    _skor_txt = f"karne {_skor}" if isinstance(_skor, (int, float)) else str(
        _result.get("label", "ölçümde"))
    _stage = cand.get("lifecycle_stage") or "—"
    _reason = _reason_line(cand["bucket"], _report)

    if st.button(f"{_sym} · {_best}", key=f"tm_{cand['bucket']}_{_sym}_{idx}",
                 width="stretch"):
        on_click(cand["ticker"])
        st.rerun()
    st.markdown(
        f"<div style='font-size:0.70rem;color:#cbd5e1;margin:-6px 0 3px 6px;"
        f"line-height:1.35;'>"
        f"<span style='color:{_yon_col};font-weight:800;'>Terazi {_yon_lbl}</span> · "
        f"<span style='color:#fbbf24;'>{_skor_txt}</span> · yaşam: {_stage}<br>"
        f"<span style='color:#94a3b8;'>{_reason}</span></div>",
        unsafe_allow_html=True)
    with st.expander("Neden burada?"):
        _skor_ek = "" if _skor is None else f" (puan {_skor})"
        _yas = cand.get("lifecycle_yas")
        _yas_ek = "" if _yas is None else f" · {_yas} gün"
        _celiski_txt = "evet" if _result.get("celiski") else "hayır"
        _missing_txt = ", ".join(_report.get("missing") or []) or "— (yok)"
        _taramalar = ", ".join(candidate_scan_membership(_sym, catalog)) or "—"
        st.markdown(
            f"- **Karne:** {_result.get('label','—')}{_skor_ek}\n"
            f"- **Terazi:** {_terazi.get('yon','—')} · boğa {_result.get('boga','—')} / "
            f"ayı {_result.get('ayi','—')} · çelişki: {_celiski_txt}\n"
            f"- **Yaşam döngüsü:** {_stage}{_yas_ek}\n"
            f"- **Bağımsız aile sayısı:** {_report.get('n_scanner','—')}\n"
            f"- **Geçemediği kapılar:** {_missing_txt}\n"
            f"- **Çıktığı taramalar:** {_taramalar}\n"
            f"- **Veri tarihi:** {cand['item'].get('data_as_of','—')}")


def _render_bucket(st, title, cands, catalog, on_click, empty_msg, first_n=5):
    st.markdown(
        f"<div style='font-size:0.9rem;font-weight:900;color:#e2e8f0;"
        f"margin:10px 0 4px 0;'>{title} · {len(cands)}</div>",
        unsafe_allow_html=True)
    if not cands:
        st.markdown(
            f"<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            f"text-align:center;color:#94a3b8;font-size:0.75rem;'>{empty_msg}</div>",
            unsafe_allow_html=True)
        return
    for _i, _c in enumerate(cands[:first_n]):
        _render_card(st, _c, catalog, on_click, _i)
    if len(cands) > first_n:
        with st.expander(f"Tüm uygun adayları göster (+{len(cands) - first_n})"):
            for _i, _c in enumerate(cands[first_n:]):
                _render_card(st, _c, catalog, on_click, first_n + _i)


def render_tarama_merkezi(session_getter, validate_fn, on_click):
    """Toplu tarama KARAR MASASI. Eski/eksik cache'te hüküm göstermez → 'yeniden tara'.
    session_getter: key->obj (st.session_state.get) · validate_fn: payload->(ok,msg)
    · on_click: ticker->None (mevcut hisse seçim sistemi)."""
    import streamlit as st

    st.markdown(
        "<div style='font-size:1.0rem;font-weight:900;color:#38bdf8;"
        "margin:4px 0 2px 0;'>🛰️ TARAMA MERKEZİ — Tek Karar Masası</div>",
        unsafe_allow_html=True)

    payload = None
    try:
        payload = session_getter("toplu_terazi_data")
    except Exception:
        payload = None
    _ok, _msg = validate_fn(payload)
    if not _ok:
        st.info(f"⏳ {_msg}")
        return

    try:
        _lc_df = session_getter("liderlik_yolculugu_data")
    except Exception:
        _lc_df = None
    _lc_lookup = build_lifecycle_lookup(_lc_df)
    desk = build_decision_desk(payload, _lc_lookup)
    catalog = build_catalog(session_getter)

    _c = desk["counts"]
    st.caption(
        f"Fotoğraf: {desk.get('as_of','—')} · aday {desk['meta'].get('candidate_count','—')} · "
        f"LONG {_c.get(BUCKET_LONG,0)} · Teyit {_c.get(BUCKET_TEYIT,0)} · "
        f"Yeni {_c.get(BUCKET_YENI,0)} · Risk {_c.get(BUCKET_RISK,0)}")

    # 1) ÖNCELİKLİ LONG — sayfanın ilk bölümü (sahte sayı yok, dinamik)
    st.markdown(
        "<div style='font-size:0.66rem;color:#64748b;margin:2px 0 0 2px;'>"
        "Bütün kapıları geçen güncel kurulumlar · <b>teyit zamanı sıralı</b> "
        "(“en iyi işlem” iddiası değildir)</div>", unsafe_allow_html=True)
    _render_bucket(st, BUCKET_LABELS[BUCKET_LONG], desk[BUCKET_LONG], catalog, on_click,
                   "Bugün bütün kapıları geçen kurulum yok.")

    _render_bucket(st, BUCKET_LABELS[BUCKET_TEYIT], desk[BUCKET_TEYIT], catalog, on_click,
                   "Teyit bekleyen kurulum yok.")

    # Yeni Sinyaller — erken dönüş alt vurgusu
    _yeni = desk[BUCKET_YENI]
    _erken = [c for c in _yeni if c["report"].get("yeni_alt") == "erken_donus"]
    st.markdown(
        f"<div style='font-size:0.9rem;font-weight:900;color:#e2e8f0;"
        f"margin:10px 0 4px 0;'>{BUCKET_LABELS[BUCKET_YENI]} · {len(_yeni)}</div>",
        unsafe_allow_html=True)
    if _erken:
        st.markdown(
            f"<div style='font-size:0.72rem;color:#a78bfa;font-weight:700;"
            f"margin:2px 0 2px 4px;'>🔄 Erken Dönüş Kurulumları — Ölçümde "
            f"({len(_erken)}) · Öncelikli LONG değildir</div>",
            unsafe_allow_html=True)
    if not _yeni:
        st.markdown(
            "<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.75rem;'>Yeni sinyal yok.</div>",
            unsafe_allow_html=True)
    else:
        for _i, _cc in enumerate(_yeni[:5]):
            _render_card(st, _cc, catalog, on_click, _i)
        if len(_yeni) > 5:
            with st.expander(f"Tüm yeni sinyalleri göster (+{len(_yeni) - 5})"):
                for _i, _cc in enumerate(_yeni[5:]):
                    _render_card(st, _cc, catalog, on_click, 5 + _i)

    _render_bucket(st, BUCKET_LABELS[BUCKET_RISK], desk[BUCKET_RISK], catalog, on_click,
                   "Sert risk vetosu alan hisse yok.", first_n=10)

    # 2) TARAMA KATALOĞU — denetim alanı (hisse tekrarı serbest)
    st.markdown(
        "<div style='font-size:0.9rem;font-weight:900;color:#e2e8f0;"
        "margin:14px 0 4px 0;'>🗂️ Tarama Kataloğu</div>", unsafe_allow_html=True)
    for _cat in catalog:
        if _cat["count"] == 0:
            continue
        with st.expander(f"{_cat['name']} · {_cat['count']} sonuç · {_cat['family']}"):
            for _j, _s in enumerate(_cat["symbols"]):
                _also = [n for n in candidate_scan_membership(_s, catalog)
                         if n != _cat["name"]]
                if st.button(_s, key=f"tmcat_{_cat['key']}_{_s}_{_j}", width="stretch"):
                    on_click(_s + ".IS" if "." not in _s else _s)
                    st.rerun()
                if _also:
                    st.markdown(
                        f"<div style='font-size:0.64rem;color:#64748b;"
                        f"margin:-6px 0 4px 6px;'>ayrıca: {', '.join(_also)}</div>",
                        unsafe_allow_html=True)
