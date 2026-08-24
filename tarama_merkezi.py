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


_TR_AYLAR = ["", "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
             "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def _human_date(iso):
    """'2026-07-30T00:00:00' → '30 Temmuz kapanış verisi' (saat/saniye YOK)."""
    try:
        _d = str(iso or "").split("T")[0].split(" ")[0]
        _y, _m, _g = _d.split("-")
        return f"{int(_g)} {_TR_AYLAR[int(_m)]} kapanış verisi"
    except Exception:
        return "güncel kapanış verisi"


def _karne_word(skor, label):
    """Karne puanını yanıltıcı '%' gibi göstermeden sade güç kelimesine çevirir."""
    if not isinstance(skor, (int, float)):
        return ("Ölçümde", "#94a3b8")
    if skor >= 70:
        return ("Kanıt gücü: Güçlü", "#22c55e")
    if skor >= 55:
        return ("Kanıt gücü: Orta", "#f59e0b")
    return ("Kanıt gücü: Zayıf", "#94a3b8")


def _setup_label(best, sources):
    """İç tarama kodunu (Erken Radar B8) senaryonun sade adına çevirir."""
    _b = str(best or "").strip()
    try:
        import re as _re
        import scanners as _sc
        _m = _re.search(r'\b([A-Z]\d{1,2})\b', _b)
        if _m and _m.group(1) in getattr(_sc, "ERKEN_RADAR_SCENARIOS", {}):
            return _sc.ERKEN_RADAR_SCENARIOS[_m.group(1)]["name"]
    except Exception:
        pass
    if _b:
        return _b
    _s = list(sources or [])
    _MAP = {"guclu_donus": "Güçlü Dönüş adayı", "minervini": "Minervini SEPA",
            "rsi_pozitif_uyumsuzluk": "RSI Pozitif Uyumsuzluk", "goldmine": "Kurulum"}
    return _MAP.get(_s[0], "Kurulum") if _s else "Kurulum"


def _split_votes(terazi):
    """terazi votes → (destekleyen boğa oyları, karşıt ayı oyları). Her oy: ad + neden."""
    _v = (terazi or {}).get("votes") or []
    _boga = [x for x in _v if isinstance(x, dict) and x.get("yon") == "boga"]
    _ayi = [x for x in _v if isinstance(x, dict) and x.get("yon") == "ayi"]
    _boga.sort(key=lambda z: -float(z.get("agirlik", 0) or 0))
    _ayi.sort(key=lambda z: -float(z.get("agirlik", 0) or 0))
    return _boga, _ayi


def _tez_cumle(bucket, terazi, report):
    """Kartın/pencerenin sade yatırım tezi cümlesi (jargonsuz)."""
    if bucket == BUCKET_LONG:
        return ("Teknik kanıtlar yukarı yönü destekliyor ve belirgin bir karşıt "
                "sinyal yok. Ölçülmüş bir kurulum.")
    if bucket == BUCKET_RISK:
        return report.get("veto_reason") or "Piyasa geneli şok — hüküm askıda."
    if bucket == BUCKET_YENI:
        if report.get("yeni_alt") == "erken_donus":
            return ("Erken bir dönüş sinyali belirdi; henüz geçmiş performansı "
                    "ölçülmedi. Öncelikli LONG değildir, izlemede.")
        return ("Yeni bir kurulum belirdi ama geçmiş kanıtı henüz yeterli değil — "
                "izlemede.")
    _m = report.get("missing") or []
    return "Potansiyel var ama teyit eksik: " + (_m[0].lower() if _m else "teyit tamamlanmadı") + "."


def _human_missing(report):
    """LONG kapılarını geçemeyen için 'ne eksik' — kullanıcı diline yakın."""
    _m = report.get("missing") or []
    if not _m:
        # kanıtlı ama yaşam döngüsü bilinmiyorsa dürüst not
        if report.get("lifecycle") is None and report.get("label") == "kanıtlı":
            return "Sinyalin zamanlaması (taze mi geç mi) doğrulanamadı."
        return "Teyit tamamlanmadı."
    return _m[0]


# ── RENDER kartları + popup ──────────────────────────────────────────────────
def _card_html(cand, catalog):
    """Tek kartın HTML gövdesi (buton hariç). Streamlit'siz üretilebilir → test kolay."""
    _sym = cand["sym"]
    _bucket = cand["bucket"]
    _report = cand["report"]
    _result = cand["item"].get("result") or {}
    _terazi = _result.get("terazi") or {}
    _best = _result.get("best")
    _setup = _setup_label(_best, cand["item"].get("sources"))
    _kw, _kc = _karne_word(_result.get("skor"), _result.get("label"))
    _stage = cand.get("lifecycle_stage")
    _fresh = _stage if _stage else ("zamanlama teyit edilmedi" if _bucket == BUCKET_LONG
                                    else "—")
    _boga, _ayi = _split_votes(_terazi)
    _top = (_boga[0]["neden"] if _boga else _tez_cumle(_bucket, _terazi, _report))
    _tarama = " · ".join(candidate_scan_membership(_sym, catalog)[:4]) or "—"
    _left = {BUCKET_LONG: "#22c55e", BUCKET_TEYIT: "#f59e0b",
             BUCKET_YENI: "#38bdf8", BUCKET_RISK: "#ef4444"}.get(_bucket, "#26364f")

    if _bucket == BUCKET_TEYIT:
        _mid = (f"<div style='color:#f8a5a5;font-size:0.70rem;margin-top:3px;'>"
                f"⚠ Eksik: {_human_missing(_report)}</div>")
    elif _bucket == BUCKET_YENI:
        _mid = (f"<div style='color:#93c5fd;font-size:0.70rem;margin-top:3px;'>"
                f"Geçmiş performansı henüz ölçülmedi — izlemede.</div>")
    elif _bucket == BUCKET_RISK:
        _mid = (f"<div style='color:#f8a5a5;font-size:0.70rem;margin-top:3px;'>"
                f"{_report.get('veto_reason','Sert risk')}</div>")
    else:
        _mid = (f"<div style='color:#cbd5e1;font-size:0.71rem;margin-top:3px;"
                f"line-height:1.4;'>{_top}</div>")

    return (
        f"<div style='background:#0f1a2e;border:1px solid #26364f;border-left:3px solid "
        f"{_left};border-radius:9px;padding:9px 11px;min-height:118px;'>"
        f"<div style='font-weight:800;font-size:0.95rem;color:#e2e8f0;'>{_sym}</div>"
        f"<div style='font-size:0.76rem;font-weight:700;color:#cbd5e1;margin-top:1px;'>{_setup}</div>"
        f"<div style='display:inline-block;font-size:0.64rem;font-weight:800;color:{_kc};"
        f"background:{_kc}1e;border:1px solid {_kc}55;border-radius:4px;padding:0 6px;"
        f"margin-top:4px;'>{_kw}</div>"
        f"{_mid}"
        f"<div style='font-size:0.63rem;color:#6b7d94;margin-top:5px;'>Güncellik: {_fresh}</div>"
        f"<div style='font-size:0.63rem;color:#6b7d94;margin-top:2px;border-top:1px solid "
        f"#ffffff0d;padding-top:4px;'><b style='color:#93a6bd;'>Taramalar:</b> {_tarama}</div>"
        f"</div>")


def _render_grid(st, cands, catalog, open_detail, first_n=6, cols=3, key_prefix=""):
    """Kartları N sütunlu grid'de render eder + 'Kurulumu aç' popup butonu."""
    _shown = cands[:first_n]
    for _r in range(0, len(_shown), cols):
        _row = _shown[_r:_r + cols]
        _ccols = st.columns(cols)
        for _k, _cand in enumerate(_row):
            with _ccols[_k]:
                st.markdown(_card_html(_cand, catalog), unsafe_allow_html=True)
                if st.button("🔍 Kurulumu aç", width="stretch",
                             key=f"tmopen_{key_prefix}_{_cand['sym']}_{_r + _k}"):
                    open_detail(_cand)
    if len(cands) > first_n:
        with st.expander(f"Tümünü göster (+{len(cands) - first_n})"):
            for _i, _cand in enumerate(cands[first_n:]):
                if st.button(f"{_cand['sym']} · {_setup_label(_cand['item'].get('result',{}).get('best'), _cand['item'].get('sources'))}",
                             width="stretch", key=f"tmmore_{key_prefix}_{_cand['sym']}_{_i}"):
                    open_detail(_cand)


def _render_bucket_header(st, title, n, note=None):
    st.markdown(
        f"<div style='font-size:0.92rem;font-weight:900;color:#e2e8f0;"
        f"margin:14px 0 2px 0;'>{title} · {n}</div>", unsafe_allow_html=True)
    if note:
        st.markdown(
            f"<div style='font-size:0.66rem;color:#64748b;margin:0 0 6px 2px;'>{note}</div>",
            unsafe_allow_html=True)


def render_setup_detail_body(st, cand, catalog, on_click):
    """Kurulum-detay pop-up gövdesi (st.dialog İÇİNDE çağrılır). Hem Tarama Merkezi
    batch grid'i hem app.py tekli ŞAMPİYONLAR LİGİ butonu ortak kullanır.
    st: streamlit modülü çağırandan gelir (modülün lazy-import desenini korur)."""
    # Genişliği ~%60'a daralt (large çok geniş). Streamlit modal'ını CSS ile sıkıştır.
    st.markdown(
        "<style>div[data-testid='stDialog'] div[role='dialog']"
        "{width:60vw !important;max-width:820px !important;}</style>",
        unsafe_allow_html=True)
    _sym = cand["sym"]
    _bucket = cand["bucket"]
    _report = cand["report"]
    _result = cand["item"].get("result") or {}
    _terazi = _result.get("terazi") or {}
    _setup = _setup_label(_result.get("best"), cand["item"].get("sources"))
    _tag = {BUCKET_LONG: ("GÜÇLÜ LONG KURULUMU", "#22c55e"),
            BUCKET_TEYIT: ("TEYİT BEKLİYOR", "#f59e0b"),
            BUCKET_YENI: ("YENİ SİNYAL", "#38bdf8"),
            BUCKET_RISK: ("RİSK", "#ef4444")}.get(_bucket, ("KURULUM", "#94a3b8"))
    _boga, _ayi = _split_votes(_terazi)

    st.markdown(
        f"<div style='font-size:0.66rem;font-weight:800;color:{_tag[1]};"
        f"letter-spacing:0.04em;'>{_tag[0]} · DETAY</div>"
        f"<div style='font-size:1.25rem;font-weight:900;'>{_sym} — {_setup}</div>"
        f"<div style='color:#94a3b8;font-size:0.72rem;'>{_human_date(cand['item'].get('data_as_of'))}</div>",
        unsafe_allow_html=True)

    st.markdown("**Ana yatırım tezi**")
    st.markdown(
        f"<div style='color:#cbd5e1;font-size:0.86rem;'>{_tez_cumle(_bucket, _terazi, _report)}</div>",
        unsafe_allow_html=True)

    if _bucket == BUCKET_TEYIT:
        st.markdown("**Ne eksik?**")
        st.markdown(
            f"<div style='color:#f8a5a5;font-size:0.84rem;'>⚠ {_human_missing(_report)}</div>",
            unsafe_allow_html=True)

    st.markdown("**Kararı destekleyen yöntemler**")
    if _boga:
        st.markdown("".join(
            f"<div style='margin:3px 0;font-size:0.82rem;'>"
            f"<span style='color:#22c55e;font-weight:700;'>✓ {v.get('ad','—')}</span> "
            f"<span style='color:#cbd5e1;'>— {v.get('neden','')}</span></div>"
            for v in _boga[:6]), unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#94a3b8;font-size:0.82rem;'>Ölçülmüş destekleyici kanıt yok.</div>",
                    unsafe_allow_html=True)

    st.markdown("**Karşıt / risk sinyalleri**")
    if _ayi:
        st.markdown("".join(
            f"<div style='margin:3px 0;font-size:0.82rem;'>"
            f"<span style='color:#f87171;font-weight:700;'>• {v.get('ad','—')}</span> "
            f"<span style='color:#cbd5e1;'>— {v.get('neden','')}</span></div>"
            for v in _ayi[:5]), unsafe_allow_html=True)
    else:
        st.markdown("<div style='color:#94a3b8;font-size:0.82rem;'>Belirgin karşıt sinyal yok.</div>",
                    unsafe_allow_html=True)

    _stage = cand.get("lifecycle_stage")
    _yas = cand.get("lifecycle_yas")
    _fresh = (f"{_stage} · {_yas} gün" if _stage and _yas is not None
              else (_stage or "teyit edilmedi"))
    st.markdown(
        f"<div style='margin-top:8px;'><b>İşlem haritası & zamanlama</b></div>"
        f"<div style='color:#94a3b8;font-size:0.80rem;line-height:1.5;'>"
        f"Sinyal güncelliği: {_fresh}<br>"
        f"<span style='color:#f8a5a5;font-style:italic;'>Hedef / stop / risk-ödül: bu "
        f"kurulum için ölçülmemiş — sayı uydurulmaz.</span></div>",
        unsafe_allow_html=True)

    _tarama = " · ".join(candidate_scan_membership(_sym, catalog)) or "—"
    with st.expander("Teknik ayrıntı"):
        _sk = _result.get("skor")
        st.markdown(
            f"- Kanıt puanı (karne): {_sk if _sk is not None else '—'}/100 · sınıf: {_result.get('label','—')}\n"
            f"- Terazi: {_terazi.get('yon','—')} · boğa {_result.get('boga','—')} / ayı {_result.get('ayi','—')} · çelişki: {'evet' if _result.get('celiski') else 'hayır'}\n"
            f"- Bağımsız kanıt ailesi: {_report.get('n_scanner','—')}\n"
            f"- Çıktığı taramalar: {_tarama}\n"
            f"- Veri tarihi: {_human_date(cand['item'].get('data_as_of'))}")

    if st.button("📊 Tam hisse analizini aç", width="stretch",
                 key=f"tmfull_{_sym}"):
        on_click(cand["ticker"])
        st.session_state["_tm_scroll_top"] = True  # seçilen hisse üstte → sayfayı yukarı kaydır
        st.rerun(scope="app")  # fragment içindeyken de TÜM sayfayı yenile (üst paneller)


def render_tarama_merkezi(session_getter, validate_fn, on_click):
    """Toplu tarama KARAR MASASI (kart grid + kurulum popup'ı).
    session_getter: key->obj · validate_fn: payload->(ok,msg) · on_click: ticker->None."""
    import streamlit as st

    st.markdown(
        "<div style='font-size:1.05rem;font-weight:900;color:#38bdf8;"
        "margin:4px 0 1px 0;'>🧭 Tarama Merkezi</div>", unsafe_allow_html=True)

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

    st.markdown(
        f"<div style='font-size:0.74rem;color:#94a3b8;margin-bottom:8px;'>"
        f"{_human_date(desk.get('as_of'))} · "
        f"<span style='color:#22c55e;font-weight:700;'>Güçlü {_c.get(BUCKET_LONG,0)}</span> · "
        f"<span style='color:#f59e0b;font-weight:700;'>Teyit {_c.get(BUCKET_TEYIT,0)}</span> · "
        f"<span style='color:#38bdf8;font-weight:700;'>Yeni {_c.get(BUCKET_YENI,0)}</span> · "
        f"<span style='color:#ef4444;font-weight:700;'>Risk {_c.get(BUCKET_RISK,0)}</span>"
        f"</div>", unsafe_allow_html=True)

    # ── KURULUM DETAY POPUP'I (st.dialog) — gövde modül seviyesinde (app.py da kullanır)
    @st.dialog("🔎 Kurulum Detayı", width="large")
    def _open_detail(cand):
        render_setup_detail_body(st, cand, catalog, on_click)

    # ── GÜÇLÜ LONG ───────────────────────────────────────────────────────────
    _render_bucket_header(
        st, "🚀 Güçlü LONG Kurulumları", _c.get(BUCKET_LONG, 0),
        "Sıralama: geçmiş performans → tazelik. \"En iyi işlem\" sıralaması değildir.")
    if desk[BUCKET_LONG]:
        _render_grid(st, desk[BUCKET_LONG], catalog, _open_detail, key_prefix="long")
    else:
        st.markdown(
            "<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.75rem;'>"
            "Bugün güçlü kurulum yok.</div>", unsafe_allow_html=True)

    # ── TEYİT BEKLEYENLER ────────────────────────────────────────────────────
    _render_bucket_header(st, "⏳ Teyit Bekleyenler", _c.get(BUCKET_TEYIT, 0),
                          "Potansiyel var; aşağıdaki teyit henüz eksik.")
    if desk[BUCKET_TEYIT]:
        _render_grid(st, desk[BUCKET_TEYIT], catalog, _open_detail, key_prefix="teyit")
    else:
        st.markdown(
            "<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.75rem;'>"
            "Teyit bekleyen kurulum yok.</div>", unsafe_allow_html=True)

    # ── YENİ SİNYALLER ───────────────────────────────────────────────────────
    _yeni = desk[BUCKET_YENI]
    _erken = [c for c in _yeni if c["report"].get("yeni_alt") == "erken_donus"]
    _note = "Bugün belirdi; geçmiş kanıtı henüz yeterli değil."
    if _erken:
        _note += f" · 🔄 {len(_erken)} erken dönüş kurulumu (ölçümde, Öncelikli LONG değil)."
    _render_bucket_header(st, "🌱 Yeni Sinyaller", len(_yeni), _note)
    if _yeni:
        _render_grid(st, _yeni, catalog, _open_detail, key_prefix="yeni")
    else:
        st.markdown(
            "<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.75rem;'>Yeni sinyal yok.</div>",
            unsafe_allow_html=True)

    # ── RİSK MASASI ──────────────────────────────────────────────────────────
    _render_bucket_header(st, "⚠️ Risk Masası", _c.get(BUCKET_RISK, 0))
    if desk[BUCKET_RISK]:
        _render_grid(st, desk[BUCKET_RISK], catalog, _open_detail, first_n=9, key_prefix="risk")
    else:
        st.markdown(
            "<div style='border:1px dashed #334155;border-radius:6px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.75rem;'>"
            "Sert risk vetosu alan hisse yok.</div>", unsafe_allow_html=True)

    # ── TARAMA KATALOĞU — denetim alanı (hisse tekrarı serbest) ──────────────
    st.markdown(
        "<div style='font-size:0.92rem;font-weight:900;color:#e2e8f0;"
        "margin:16px 0 2px 0;'>📚 Tarama Kataloğu</div>"
        "<div style='font-size:0.66rem;color:#64748b;margin:0 0 6px 2px;'>"
        "Karara oy verenler ile ek gözlemler burada; hisse birden çok listede olabilir.</div>",
        unsafe_allow_html=True)
    for _cat in catalog:
        if _cat["count"] == 0:
            continue
        with st.expander(f"{_cat['name']} · {_cat['count']} sonuç · {_cat['family']}"):
            for _j, _s in enumerate(_cat["symbols"]):
                _also = [n for n in candidate_scan_membership(_s, catalog)
                         if n != _cat["name"]]
                if st.button(_s, key=f"tmcat_{_cat['key']}_{_s}_{_j}", width="stretch"):
                    on_click(_s + ".IS" if "." not in _s else _s)
                    st.rerun(scope="app")
                if _also:
                    st.markdown(
                        f"<div style='font-size:0.64rem;color:#64748b;"
                        f"margin:-6px 0 4px 6px;'>ayrıca: {', '.join(_also)}</div>",
                        unsafe_allow_html=True)
