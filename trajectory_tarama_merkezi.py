# -*- coding: utf-8 -*-
"""
trajectory_tarama_merkezi.py — Tarama Merkezi'nin T+3 yolculuk katmanı.

Bu modül, Master Scan'in bulduğu ham adayları "en çok taramada çıkan" diye
puanlamaz. `gelişmiş tarama/trajectory_forward_snapshots.csv` içindeki kapanış
fotoğraflarını okuyup adayın sonraki günlerdeki davranışını sınıflandırır:

    T0              → Bugün yeni yakalanan aday
    T+1 / T+2        → Takipte güçlenen aday
    T+3              → Karar hazır aday

Kalabalıklaşma yalnızca uyarıdır; sıralama puanı değildir. Sert risk vetosu ve
boğa-ayı çelişkisi, mevcut Kanıt Terazisi çıktısından okunur. Bu modül patron.db,
ana parquet'ler veya uygulama cache'lerine yazmaz; salt-okurdur.
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

import tarama_merkezi
import evidence


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "patron.db"
SNAPSHOT_PATH = ROOT / "gelişmiş tarama" / "trajectory_forward_snapshots.csv"
KARNE_PATH = ROOT / "gelişmiş tarama" / "trajectory_live_karne.json"
FORWARD_START_DATE = "2026-08-07"
CROWDING_WARNING_MIN = 5

# Vade, karar sekmelerinin ikinci eksenidir; aday birden fazla kaynak taşısa da
# yalnız tek masada görünür. Sıra kanıtlı kısa vade → sabır → katalogdur.
_VADE_MASA_ORDER = ("KISA", "SABIR", "KATALOG")
_VADE_MASA_LABELS = {
    "KISA": "⏱ KISA MASASI · T+3 / T+5",
    "SABIR": "⏳ SABIR MASASI · T+20",
    "KATALOG": "📚 KATALOG · GÖZLEM",
}
_VADE_MASA_RANK = {name: index for index, name in enumerate(_VADE_MASA_ORDER)}


def _canonical_scan_type(value: object) -> str:
    """Ham kaynak adını evidence.py'deki politika anahtarına bağlar."""
    import re

    text = str(value or "").strip()
    if not text:
        return ""
    if text in evidence.SCANNER_VADE_POLICY:
        return text
    lowered = text.lower()
    normalized = re.sub(r"[^a-z0-9]+", "_", lowered).strip("_")
    aliases = {
        "radar_1": "radar1", "radar1": "radar1",
        "radar_2": "radar2", "radar2": "radar2",
        "altin_setup": "altin_setup", "platin_setup": "platin_setup",
        "tekli_altin": "tekli_altin", "guclu_donus": "guclu_donus",
        "minervini": "minervini", "minervini_sepa": "minervini",
        "tavan_top30": "tavan_top30", "tavan_alarm": "tavan_alarm",
        "prelaunch_bos": "prelaunch_bos", "pre_launch_bos": "prelaunch_bos",
        "liderlik_adayi": "liderlik_aday", "liderlik_aday": "liderlik_aday",
    }
    if normalized in aliases:
        return aliases[normalized]
    # Best alanı çoğunlukla "Erken Radar C6 (… )" biçiminde gelir.
    scenario = re.search(r"\b([abcd]\d{1,2})\b", lowered)
    if scenario and ("radar" in lowered or "erken" in lowered or normalized.startswith("er_")):
        return f"er_{scenario.group(1).upper()}"
    if "pre" in normalized and "launch" in normalized and "bos" in normalized:
        return "prelaunch_bos"
    if "liderlik" in normalized and "aday" in normalized:
        return "liderlik_aday"
    if normalized in {key.lower() for key in evidence.SCANNER_VADE_POLICY}:
        return next(key for key in evidence.SCANNER_VADE_POLICY if key.lower() == normalized)
    return text


def _date_only(value: object) -> str | None:
    """Tarih benzeri değeri ISO gününe çevirir; hatalı değerleri taşımaz."""
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()
    except Exception:
        return None


def _session_dates_from_payload(payload: object) -> list[object] | None:
    """Fiyat kasasının seans listesi payload'a eklenmişse onu kullanır."""
    if not isinstance(payload, dict):
        return None
    for key in ("session_dates", "price_session_dates", "bist_session_dates"):
        values = payload.get(key)
        if isinstance(values, (list, tuple, set)):
            clean = [value for value in values if _date_only(value)]
            if clean:
                return clean
    return None


def _scanner_karne_text(scan_keys: list[str]) -> str:
    """Kartta geçmiş ölçümü gösterir; ölçülmemişse bunu açıkça söyler."""
    for key in scan_keys:
        tier_row = evidence.SCANNER_TIER_MAP.get(key)
        if tier_row:
            _tier, hit, ret, _name, _note = tier_row
            return f"T+20 hit %{float(hit):.1f} · ort. getiri %{float(ret):+.1f}"
        alpha = evidence.alfa_deger(key)
        if alpha is not None:
            return f"T+20 alfa %{float(alpha):+.2f}"
    return "BİLMİYORUZ · ölçüm kaydı yok"


def _vade_records(scan_types: list[str], signal_date: object,
                  session_dates: list[object] | None) -> list[dict[str, Any]]:
    """Adayın kaynaklarını tek tek policy/expiry kayıtlarına çevirir."""
    raw_types = [str(value).strip() for value in (scan_types or []) if str(value).strip()]
    if not raw_types:
        raw_types = ["KATALOG"]
    records = []
    for raw in raw_types:
        key = _canonical_scan_type(raw)
        metadata = evidence.scanner_vade_metadata(key, signal_date, session_dates=session_dates)
        records.append({
            "raw": raw,
            "key": key,
            "masa": metadata.get("masa", "KATALOG"),
            "vade": metadata.get("vade"),
            "vade_gun": metadata.get("vade_gun"),
            "etiket": metadata.get("etiket") or "",
            "son_kullanma_tarihi": metadata.get("son_kullanma_tarihi"),
            "karne": _scanner_karne_text([key]),
        })
    return records


def _attach_vade_metadata(candidate: dict[str, Any], as_of: object,
                          session_dates: list[object] | None) -> dict[str, Any] | None:
    """Vade eksenini karta bağlar; bütün kaynaklar kapanmışsa kartı düşürür."""
    signal_date = candidate.get("event_start_date")
    if str(signal_date or "").strip() in {"", "—", "nan", "None"}:
        signal_date = None
    records = _vade_records(candidate.get("scan_types") or [], signal_date, session_dates)
    as_of_day = _date_only(as_of)
    active = []
    expired = []
    for record in records:
        expiry = record.get("son_kullanma_tarihi")
        # Son gün dahildir; sonraki kapanışta sinyal kapanır.
        if expiry and as_of_day and as_of_day > str(expiry)[:10]:
            expired.append(record)
        else:
            active.append(record)
    if not active:
        return None
    # Bir adayın farklı kaynakları varsa en kısa açık vade, sonra masa sırası.
    chosen = min(
        active,
        key=lambda record: (
            _VADE_MASA_RANK.get(record.get("masa", "KATALOG"), 99),
            int(record.get("vade_gun") or 999),
            str(record.get("key") or ""),
        ),
    )
    candidate = dict(candidate)
    candidate.update({
        "vade_masasi": chosen.get("masa", "KATALOG"),
        "vade": chosen.get("vade") or "T+20",
        "vade_gun": chosen.get("vade_gun"),
        "vade_etiketi": chosen.get("etiket") or "",
        "son_kullanma_tarihi": chosen.get("son_kullanma_tarihi"),
        "gecmis_karne": chosen.get("karne") or "BİLMİYORUZ · ölçüm kaydı yok",
        "vade_kaynak": chosen.get("key") or chosen.get("raw"),
        "vade_kayitlari": records,
        "suresi_dolmus_kaynaklar": [record.get("key") for record in expired],
    })
    return candidate

BUCKET_READY = "karar_hazir"
BUCKET_GROWING = "guclenen"
BUCKET_NEW = "yeni_aday"
BUCKET_WATCH = "izleme"
BUCKET_RISK = "risk"

BUCKETS = (BUCKET_READY, BUCKET_GROWING, BUCKET_NEW, BUCKET_WATCH, BUCKET_RISK)

BUCKET_META = {
    BUCKET_READY: {
        "title": "🎯 Karar Hazır Adaylar",
        "note": "T+3 tamamlandı; fiyat davranışı güç sinyalini korudu.",
        "color": "#22c55e",
        "tag": "KARAR HAZIR",
    },
    BUCKET_GROWING: {
        "title": "📈 Takipte Güçlenenler",
        "note": "T+1 / T+2: fiyat davranışı olumlu; henüz karar aşaması değil.",
        "color": "#f59e0b",
        "tag": "GÜÇLENİYOR",
    },
    BUCKET_NEW: {
        "title": "🔎 Bugün Yeni Yakalanan Adaylar",
        "note": "T0: takip başladı. İlk gün hiçbir hisseye güçlü LONG etiketi verilmez.",
        "color": "#38bdf8",
        "tag": "YENİ ADAY",
    },
    BUCKET_WATCH: {
        "title": "↔ Karşı Sinyalli / İzleme Gerekli",
        "note": "Çelişki var veya ilk takip günlerinde güç henüz yeterli değil.",
        "color": "#f59e0b",
        "tag": "İZLEME",
    },
    BUCKET_RISK: {
        "title": "⚠️ Risk Masası",
        "note": "Sert risk vetosu alanlar diğer listelere karışmaz.",
        "color": "#ef4444",
        "tag": "RİSK",
    },
}


def _clean_symbol(value: object) -> str:
    return str(value or "").upper().replace(".IS", "").strip()


def _as_int(value: object, default: int = 0) -> int:
    value = pd.to_numeric(value, errors="coerce")
    return int(value) if pd.notna(value) else default


def _as_float(value: object, default: float = 0.0) -> float:
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) else default


def _display_scan(scan_type: object) -> str:
    text = str(scan_type or "").strip()
    if not text:
        return "—"
    return text.replace("_", " ").title()


def _story_for_scans(scan_types: list[str]) -> str | None:
    """Tarama adını değil, adayın anlattığı erken-hareket hikâyesini açık Türkçe ve parantezli kısaltmalarla döndürür."""
    lowered = " ".join(str(x).lower().replace("_", " ") for x in scan_types)
    if any(token in lowered for token in ("zirve devam", "zirve devami", "52h breakout")):
        return "🚀 Zirve Kırılımı / Trend Lideri (BoS - Yapı Kırılımı)"
    if any(token in lowered for token in ("minervini", "sepa")):
        return "📈 Kurumsal Büyüme Trendi (Minervini SEPA)"
    if any(token in lowered for token in ("platin", "nadir firsat", "nadir_firsat")):
        return "⚡ Büyük Sıkışma Patlaması (Platin Setup)"
    if any(token in lowered for token in ("er b11", "radar b11", "gizli birikim", "accum", "para akisi")):
        return "💰 Büyük Para Girişi / Toplama (Akümülasyon)"
    if any(token in lowered for token in ("er c6", "radar c6", "radar2", "radar 2", "liderlik", "rs leaders", "rs momentum")):
        return "💎 Piyasadan Ayrışan Lider (RS - Göreceli Güç)"
    if any(token in lowered for token in ("er b8", "radar b8", "prelaunch", "pre launch", "bayrak", "formasyon")):
        return "🎯 Sıkışma Sonrası Çözülme (Pre-Launch BoS)"
    if any(token in lowered for token in ("er c2", "radar c2", "er c5", "radar c5", "ortalama testi", "pullback")):
        return "🛡️ Alıcı Desteğinde Devam (Order Block / FVG)"
    if any(token in lowered for token in ("er a1", "radar a1", "guclu donus", "wilder", "pozitif uyumsuzluk")):
        return "🔄 Dipten İlk Uyanış / Dönüş (ChoCH - Trend Değişimi)"
    return "📊 Algoritmik Güçlü Kurulum"


def _load_snapshot_rows() -> tuple[pd.DataFrame, str | None]:
    """En güncel kesin kapanış fotoğrafını, hisse + olay bazında tekilleştirerek okur."""
    empty = pd.DataFrame()
    if not SNAPSHOT_PATH.exists():
        return empty, None
    try:
        frame = pd.read_csv(SNAPSHOT_PATH)
    except Exception:
        return empty, None
    required = {"symbol", "event_start_date", "snapshot_date", "event_day", "status"}
    if frame.empty or not required.issubset(frame.columns):
        return empty, None
    frame = frame[frame.get("feature_source", "").astype(str) == "live_close"].copy()
    if frame.empty:
        return empty, None
    frame["snapshot_date"] = frame["snapshot_date"].astype(str).str[:10]
    frame["symbol"] = frame["symbol"].map(_clean_symbol)
    frame["event_start_date"] = frame["event_start_date"].astype(str).str[:10]
    frame["_order"] = pd.to_datetime(frame.get("snapshot_at"), errors="coerce")
    frame = frame.sort_values(["symbol", "event_start_date", "snapshot_date", "_order"])
    # "Güçleniyor" demek için, aynı olayın önceki kesin kapanışını saklarız.
    frame = frame.drop_duplicates(["symbol", "event_start_date", "snapshot_date"], keep="last")
    frame["prior_trajectory_v1"] = frame.groupby(["symbol", "event_start_date"])["trajectory_v1"].shift(1)
    frame["prior_cur_core"] = frame.groupby(["symbol", "event_start_date"])["cur_core"].shift(1)
    as_of = frame["snapshot_date"].max()
    frame = frame[frame["snapshot_date"] == as_of].copy()
    frame = frame.sort_values("_order")
    return frame.drop_duplicates(["symbol", "event_start_date"], keep="last"), as_of


def _load_event_scans(events: pd.DataFrame) -> dict[tuple[str, str], list[str]]:
    """Forward olayının hangi ham Master Scan kaynaklarından geldiğini okur."""
    result: dict[tuple[str, str], list[str]] = {}
    if events.empty or not DB_PATH.exists():
        return result
    keys = {(str(row.symbol), str(row.event_start_date)) for row in events.itertuples()}
    if not keys:
        return result
    try:
        con = sqlite3.connect(DB_PATH)
        rows = pd.read_sql_query(
            """
            SELECT symbol, event_start_date, scan_type
            FROM scan_signals
            WHERE scan_date >= ?
              AND event_start_date IS NOT NULL
              AND scan_type IS NOT NULL
            """,
            con,
            params=(FORWARD_START_DATE,),
        )
        con.close()
    except Exception:
        return result
    if rows.empty:
        return result
    rows["symbol"] = rows["symbol"].map(_clean_symbol)
    rows["event_start_date"] = rows["event_start_date"].astype(str).str[:10]
    rows.loc[rows["event_start_date"] < FORWARD_START_DATE, "event_start_date"] = FORWARD_START_DATE
    rows["scan_type"] = rows["scan_type"].astype(str).str.strip()
    rows = rows[rows.apply(lambda row: (row["symbol"], row["event_start_date"]) in keys, axis=1)]
    for (symbol, event_date), group in rows.groupby(["symbol", "event_start_date"], sort=False):
        result[(symbol, event_date)] = sorted(set(group["scan_type"].tolist()))
    return result


def _payload_lookup(payload: object) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    items = payload.get("items") or {}
    return {_clean_symbol(symbol): item for symbol, item in items.items() if isinstance(item, dict)}


def _payload_risk(item: dict[str, Any] | None) -> tuple[bool, bool, str]:
    if not item:
        return False, False, ""
    result = (item or {}).get("result") or {}
    terazi = result.get("terazi") or {}
    if terazi.get("sistemik"):
        return True, False, "Piyasa şoku — sistemik gün, hisse hükmü askıda"
    
    # 🛡️ Aşırı Uzama / FOMO Kontrolü (Terazi oylarında momentum ucu veya aşırı prim)
    votes = terazi.get("votes") or []
    for vote in votes:
        name = str(vote.get("ad") or "").lower()
        if "momentum ucu" in name or "aşırı alım" in name or "uç aşırı alım" in name:
            return False, True, "⚠️ Aşırı Uzamış: Momentum ucunda (FOMO riski / düzeltme ihtimali)"

    conflict = bool(result.get("celiski") or terazi.get("celiski"))
    if conflict:
        return False, True, "Boğa-ayı çelişkisi var"
    return False, False, ""


def _payload_scan_types(item: dict[str, Any]) -> list[str]:
    """Toplu Terazi'nin dar adayından yalnız kaynak bilgisini alır.

    Eski karne puanı burada okunmaz. `best`, taramanın teknik kodunu gizlemek
    yerine adayın hangi erken-hareket hikâyesinden geldiğini anlamaya yarar.
    """
    sources = list(item.get("sources") or [])
    best = ((item.get("result") or {}).get("best"))
    if best:
        sources.append(str(best))
    return sorted(set(str(source).strip() for source in sources if str(source).strip()))


def _trajectory_is_strong(row: pd.Series) -> bool:
    """Araştırmada kullanılan mevcut tetik: v1≥3 veya cur_core≥2.

    Yeni ağırlık/eşik icat edilmez; collector ve otomasyon bildirimleriyle aynı dil.
    """
    return _as_float(row.get("trajectory_v1")) >= 3 or _as_float(row.get("cur_core")) >= 2


def _is_extended(row: pd.Series) -> tuple[bool, str]:
    """Hissenin sinyalden beri aşırı uzayıp uzamadığını (FOMO riski) kontrol eder."""
    move_pct = _as_float(row.get("move_since_t0_pct"))
    move_atr = _as_float(row.get("move_since_t0_atr"))
    price = _as_float(row.get("price"))
    sma20 = _as_float(row.get("sma20_value"))
    atr = _as_float(row.get("atr_value"))
    
    # 1. Sinyal başlangıcından beri %12'den fazla prim yapmışsa
    if move_pct >= 12.0:
        return True, f"⚠️ Aşırı Uzamış: Sinyalden beri +%{move_pct:.1f} primli (FOMO riski / tepeye yakın)"
    # 2. Sinyalden beri 2.0 ATR'den fazla ralli yapmışsa
    if move_atr >= 2.0:
        return True, f"⚠️ Aşırı Uzamış: Sinyalden beri {move_atr:.1f} ATR ralli yaptı"
    # 3. Fiyat 20 günlük ortalamasından 2.2 ATR'den fazla yukarı açılmışsa
    if atr > 0 and sma20 > 0 and price > 0:
        dist_atr = (price - sma20) / atr
        if dist_atr >= 2.2:
            return True, f"⚠️ Aşırı Uzamış: MA20'den {dist_atr:.1f} ATR yukarıda (ortalamaya dönüş riski)"
            
    return False, ""


def _status_bucket(row: pd.Series, hard_risk: bool, conflict: bool) -> tuple[str, str]:
    if hard_risk:
        return BUCKET_RISK, "Sert risk vetosu"
    if conflict:
        return BUCKET_WATCH, "Boğa-ayı çelişkisi veya aşırı uzama var"

    # 🛡️ AŞIRI UZAMA / FOMO EMNİYET KİLİDİ (23 Ağu 2026)
    extended, ext_reason = _is_extended(row)
    if extended:
        return BUCKET_WATCH, ext_reason

    event_day = _as_int(row.get("event_day"))
    strong = _trajectory_is_strong(row)
    status = str(row.get("status") or "").lower()
    if event_day >= 3 and status == "decision_ready" and strong:
        return BUCKET_READY, "T+3 karar kontrolü tamamlandı"
    prior_v1 = pd.to_numeric(row.get("prior_trajectory_v1"), errors="coerce")
    prior_core = pd.to_numeric(row.get("prior_cur_core"), errors="coerce")
    grew = bool(
        (pd.notna(prior_v1) and _as_float(row.get("trajectory_v1")) > float(prior_v1))
        or (pd.notna(prior_core) and _as_float(row.get("cur_core")) > float(prior_core))
    )
    if event_day in (1, 2) and strong and grew:
        return BUCKET_GROWING, "Önceki kapanışa göre skor güçlendi"
    if event_day <= 0:
        return BUCKET_NEW, "İlk gün aday; takip başlıyor"
    return BUCKET_WATCH, "Önceki kapanışa göre güçlenme henüz doğrulanmadı"


def _candidate_from_row(
    row: pd.Series,
    scan_types: list[str],
    payload_item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    story = _story_for_scans(scan_types)
    if not story:
        return None
    hard_risk, conflict, risk_note = _payload_risk(payload_item)
    bucket, note = _status_bucket(row, hard_risk, conflict)
    scan_count = _as_int(row.get("scan_types_last"))
    return {
        "sym": _clean_symbol(row.get("symbol")),
        "ticker": f"{_clean_symbol(row.get('symbol'))}.IS",
        "event_start_date": str(row.get("event_start_date")),
        "event_day": _as_int(row.get("event_day")),
        "bucket": bucket,
        "story": story,
        "note": risk_note or note,
        "scan_types": scan_types,
        "scan_count": scan_count,
        "crowded": scan_count >= CROWDING_WARNING_MIN,
        "price": _as_float(row.get("price")),
        "trajectory_v1": _as_int(row.get("trajectory_v1")),
        "cur_core": _as_int(row.get("cur_core")),
        "prior_trajectory_v1": _as_int(row.get("prior_trajectory_v1"), -1),
        "prior_cur_core": _as_int(row.get("prior_cur_core"), -1),
        "rsi_up": bool(_as_int(row.get("rsi_up"))),
        "rs_up": bool(_as_int(row.get("rs_up"))),
        "israr": _as_int(row.get("israr")),
        "payload_item": payload_item,
    }


def _choose_one_per_symbol(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aynı hissedeki çoklu event'i tek karta indirir; en olgun açık olayı korur."""
    rank = {BUCKET_READY: 0, BUCKET_GROWING: 1, BUCKET_NEW: 2, BUCKET_WATCH: 3, BUCKET_RISK: 4}
    chosen: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        symbol = candidate["sym"]
        current = chosen.get(symbol)
        key = (
            rank[candidate["bucket"]],
            -candidate["event_day"],
            candidate["event_start_date"],
        )
        if current is None:
            chosen[symbol] = candidate
            continue
        current_key = (
            rank[current["bucket"]],
            -current["event_day"],
            current["event_start_date"],
        )
        if key < current_key:
            chosen[symbol] = candidate
    return list(chosen.values())


def _sort_candidates(bucket: str, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if bucket in (BUCKET_READY, BUCKET_GROWING):
        return sorted(
            values,
            key=lambda item: (-item["trajectory_v1"], -item["cur_core"], -item["israr"], item["sym"]),
        )
    # Yeni adaylar yarış listesi değildir: yalnızca hikâye + sembol düzeniyle gösterilir.
    return sorted(values, key=lambda item: (item["story"], item["sym"]))


def group_candidates_by_vade(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Karar sekmesi içindeki adayları tek vade masasına ayırır."""
    groups = {masa: [] for masa in _VADE_MASA_ORDER}
    for candidate in candidates or []:
        masa = str(candidate.get("vade_masasi") or "KATALOG")
        groups.setdefault(masa, []).append(candidate)
    return {masa: groups.get(masa, []) for masa in _VADE_MASA_ORDER}


def build_trajectory_desk(payload: object, session_dates: list[object] | None = None) -> dict[str, Any]:
    """Kapanış trajectory çıktısı + Terazi uyarıları → ekran bölümleri.

    ``session_dates`` fiyat kasasının gerçek işlem günleri olabilir. Verilmezse
    evidence.py içindeki BIST takvimi kullanılır; takvim günü sayılmaz.
    """
    out: dict[str, Any] = {bucket: [] for bucket in BUCKETS}
    out.update({"as_of": None, "source": "snapshot", "catalog_only": False, "counts": {}})
    rows, as_of = _load_snapshot_rows()
    payload_items = _payload_lookup(payload)
    if session_dates is None:
        session_dates = _session_dates_from_payload(payload)
    out["as_of"] = as_of

    if rows.empty:
        # İlk kapanış fotoğrafı oluşmadan eski karar skoru ile güçlü etiketi üretme.
        # Sadece mevcut dar aday havuzunu T0 izleme listesi olarak saklar.
        out["source"] = "session_fallback"
        out["catalog_only"] = True
        lifecycle = {}
        baseline = tarama_merkezi.build_decision_desk(payload, lifecycle)
        for old_bucket in (tarama_merkezi.BUCKET_LONG, tarama_merkezi.BUCKET_YENI, tarama_merkezi.BUCKET_TEYIT):
            for old in baseline.get(old_bucket, []):
                scans = list((old.get("item") or {}).get("sources") or [])
                story = _story_for_scans(scans) or "Erken kurulum"
                target = BUCKET_WATCH if old_bucket == tarama_merkezi.BUCKET_TEYIT else BUCKET_NEW
                out[target].append({
                    "sym": old["sym"], "ticker": old["ticker"], "event_start_date": "—", "event_day": 0,
                    "bucket": target, "story": story,
                    "note": "Kapanış takip fotoğrafı henüz oluşmadı", "scan_types": scans,
                    "scan_count": 0, "crowded": False, "price": 0.0, "trajectory_v1": 0,
                    "cur_core": 0, "prior_trajectory_v1": -1, "prior_cur_core": -1,
                    "rsi_up": False, "rs_up": False, "israr": 0,
                    "payload_item": old.get("item"),
                })
        for old in baseline.get(tarama_merkezi.BUCKET_RISK, []):
            out[BUCKET_RISK].append({
                "sym": old["sym"], "ticker": old["ticker"], "event_start_date": "—", "event_day": 0,
                "bucket": BUCKET_RISK, "story": "Risk vetosu", "note": old.get("report", {}).get("veto_reason", "Sert risk vetosu"),
                "scan_types": list((old.get("item") or {}).get("sources") or []), "scan_count": 0,
                "crowded": False, "price": 0.0, "trajectory_v1": 0, "cur_core": 0,
                "prior_trajectory_v1": -1, "prior_cur_core": -1,
                "rsi_up": False, "rs_up": False, "israr": 0, "payload_item": old.get("item"),
            })
    else:
        memberships = _load_event_scans(rows)
        candidates = []
        for _, row in rows.iterrows():
            event_key = (_clean_symbol(row.get("symbol")), str(row.get("event_start_date"))[:10])
            payload_item = payload_items.get(event_key[0])
            # T0 vitrini ham radar listesinden kurulmaz: o günün Master Scan dar
            # aday havuzu gerekir. Radar1/momentum flood'u yine collector'da
            # ölçülür; ancak T+1/T+2/T+3 davranışı kanıtlanmadan ekrana taşınmaz.
            if _as_int(row.get("event_day")) <= 0:
                if payload_item is None:
                    continue
                scan_types = _payload_scan_types(payload_item)
            else:
                scan_types = memberships.get(event_key, [])
            candidate = _candidate_from_row(row, scan_types, payload_item)
            if candidate:
                candidates.append(candidate)

        # Kapanış collector'ı yalnız daha önce başlamış event'leri taşıyabilir.
        # Master Scan'in o gün ilk kez aday yaptığı hisseler, ilk fotoğraf gelene
        # kadar burada T0 olarak görünür. Eski karne/konfluens puanı kullanılmaz.
        snapshot_symbols = {candidate["sym"] for candidate in candidates}
        for symbol, item in payload_items.items():
            if symbol in snapshot_symbols:
                continue
            scan_types = _payload_scan_types(item)
            story = _story_for_scans(scan_types)
            if not story:
                continue
            hard_risk, conflict, risk_note = _payload_risk(item)
            bucket = BUCKET_RISK if hard_risk else (BUCKET_WATCH if conflict else BUCKET_NEW)
            candidates.append({
                "sym": symbol,
                "ticker": str(item.get("ticker") or f"{symbol}.IS"),
                "event_start_date": as_of or "—",
                "event_day": 0,
                "bucket": bucket,
                "story": story,
                "note": risk_note or ("Boğa-ayı çelişkisi var" if conflict else "İlk gün aday; takip başlıyor"),
                "scan_types": scan_types,
                "scan_count": 0,
                "crowded": False,
                "price": 0.0,
                "trajectory_v1": 0,
                "cur_core": 0,
                "prior_trajectory_v1": -1,
                "prior_cur_core": -1,
                "rsi_up": False,
                "rs_up": False,
                "israr": 0,
                "payload_item": item,
            })
        for candidate in _choose_one_per_symbol(candidates):
            out[candidate["bucket"]].append(candidate)

    expiry_as_of = out.get("as_of") or (payload.get("as_of") if isinstance(payload, dict) else None)
    for bucket in BUCKETS:
        active_candidates = []
        for candidate in out[bucket]:
            enriched = _attach_vade_metadata(candidate, expiry_as_of, session_dates)
            if enriched is not None:
                active_candidates.append(enriched)
        out[bucket] = _sort_candidates(bucket, active_candidates)
    out["counts"] = {bucket: len(out[bucket]) for bucket in BUCKETS}
    return out


def _card_html(candidate: dict[str, Any], *, is_showcase: bool = False) -> str:
    meta = BUCKET_META[candidate["bucket"]]
    symbol = html.escape(candidate["sym"])
    story = html.escape(candidate["story"])
    note = html.escape(candidate["note"])
    source_text = " · ".join(_display_scan(x) for x in candidate["scan_types"][:3]) or "—"
    source_text = html.escape(source_text)
    day = candidate["event_day"]
    current = "T0 (Yeni)" if day == 0 else f"T+{day} (Takipte)"
    masa = str(candidate.get("vade_masasi") or "KATALOG")
    masa_label = html.escape(_VADE_MASA_LABELS.get(masa, masa))
    vade = html.escape(str(candidate.get("vade") or "T+20"))
    expiry = candidate.get("son_kullanma_tarihi")
    expiry_text = html.escape(str(expiry)[:10] if expiry else "hesaplanamadı")
    karne = html.escape(str(candidate.get("gecmis_karne") or "BİLMİYORUZ · ölçüm kaydı yok"))
    vade_html = (
        f"<div style='font-size:0.68rem;color:#cbd5e1;margin:3px 0 2px 0;'>"
        f"<b>Vade:</b> {masa_label} · {vade}</div>"
        f"<div style='font-size:0.66rem;color:#94a3b8;margin:1px 0 2px 0;'>"
        f"<b>Son kullanma:</b> {expiry_text}</div>"
        f"<div style='font-size:0.66rem;color:#94a3b8;margin:1px 0 4px 0;'>"
        f"<b>Geçmiş karne:</b> {karne}</div>"
    )
    
    # Rozetler
    badges = []
    is_ext = "aşırı uzamış" in note.lower() or "fomo" in note.lower()
    if is_ext:
        badges.append("<span style='background:#ef44441a;color:#f87171;border:1px solid #ef444444;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:800;'>⚠️ AŞIRI UZAMIŞ (FOMO RİSKİ)</span>")
    elif candidate["bucket"] in (BUCKET_READY, BUCKET_GROWING):
        score_val = candidate.get('trajectory_v1', 0)
        core_val = candidate.get('cur_core', 0)
        badges.append(f"<span style='background:#22c55e1a;color:#4ade80;border:1px solid #22c55e44;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:800;'>⚡ {score_val}/5 GÜÇ ONAYI</span>")
        if candidate.get("rs_up"):
            badges.append("<span style='background:#38bdf81a;color:#38bdf8;border:1px solid #38bdf844;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'>📊 Endeksten Güçlü (RS)</span>")
        if candidate.get("rsi_up"):
            badges.append("<span style='background:#a855f71a;color:#c084fc;border:1px solid #a855f744;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'>📈 RSI İvmesi Pozitif</span>")
    elif candidate["bucket"] == BUCKET_NEW:
        badges.append(f"<span style='background:#38bdf81a;color:#38bdf8;border:1px solid #38bdf844;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'>🔎 T0 İlk Gün Sinyali</span>")
    else:
        badges.append(f"<span style='background:#f59e0b1a;color:#fbbf24;border:1px solid #f59e0b44;padding:2px 6px;border-radius:4px;font-size:0.68rem;font-weight:700;'>↔️ {meta['tag']}</span>")

    badge_html = " ".join(badges)
    crowded = ""
    if candidate.get("crowded"):
        crowded = (
            "<div style='color:#fbbf24;font-size:0.65rem;margin-top:3px;'>"
            f"⚠ Kalabalık: {candidate['scan_count']} taramada çıktı (güç puanı değildir)</div>"
        )

    # Vitrin kartı tasarımı (Daha ferah ve göz alıcı)
    if is_showcase:
        return (
            f"<div style='background:linear-gradient(145deg, #0f172a 0%, #1e293b 100%);"
            f"border:1px solid #334155;border-top:3px solid {meta['color']};"
            "border-radius:10px;padding:12px 14px;min-height:165px;margin-bottom:6px;box-shadow:0 4px 12px rgba(0,0,0,0.25);'>"
            "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'>"
            f"<span style='font-weight:900;font-size:1.18rem;color:#f8fafc;letter-spacing:0.5px;'>{symbol}</span>"
            f"<span style='font-size:0.65rem;font-weight:800;color:{meta['color']};background:{meta['color']}18;"
            f"border:1px solid {meta['color']}55;border-radius:4px;padding:2px 7px;'>{meta['tag']} · {current}</span>"
            "</div>"
            f"<div style='font-size:0.80rem;font-weight:700;color:#7dd3fc;margin-bottom:6px;'>{story}</div>"
            f"{vade_html}"
            f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;'>{badge_html}</div>"
            f"{crowded}"
            "<div style='background:#090d1680;border-radius:6px;padding:5px 8px;font-size:0.68rem;color:#94a3b8;border-left:2px solid #38bdf880;margin-top:4px;'>"
            f"<b style='color:#cbd5e1;'>Kaynak:</b> {source_text}"
            "</div>"
            "</div>"
        )

    # Kompakt standart kart tasarımı
    return (
        f"<div style='background:#0f172a;border:1px solid #1e293b;border-left:3px solid {meta['color']};"
        "border-radius:8px;padding:8px 10px;min-height:120px;margin-bottom:4px;'>"
        "<div style='display:flex;justify-content:space-between;align-items:center;'>"
        f"<span style='font-weight:800;font-size:0.95rem;color:#e2e8f0;'>{symbol}</span>"
        f"<span style='font-size:0.60rem;font-weight:700;color:{meta['color']};'>{current}</span>"
        "</div>"
        f"<div style='font-size:0.72rem;font-weight:700;color:#93c5fd;margin:2px 0 4px 0;'>{story}</div>"
        f"{vade_html}"
        f"<div style='margin-bottom:4px;'>{badge_html}</div>"
        f"{crowded}"
        f"<div style='font-size:0.62rem;color:#64748b;margin-top:4px;border-top:1px solid #ffffff0a;padding-top:3px;'>"
        f"<b>Kaynak:</b> {source_text}</div>"
        "</div>"
    )


def _render_grid(st: Any, candidates: list[dict[str, Any]], open_detail: Any, *, key_prefix: str) -> None:
    if not candidates:
        return
    
    total = len(candidates)
    
    # 🌟 1. VİTRİN BÖLÜMÜ: İlk 3 Aday Büyük Vitrin Kartı Olarak Gösterilir
    showcase_count = min(3, total)
    showcase_items = candidates[:showcase_count]
    
    st.markdown("<div style='font-size:0.78rem;font-weight:800;color:#94a3b8;margin:6px 0 4px 2px;'>"
                "⭐ ÖNE ÇIKAN EN GÜÇLÜ VİTRİN ADAYLARI</div>", unsafe_allow_html=True)
    
    cols = st.columns(showcase_count)
    for idx, candidate in enumerate(showcase_items):
        with cols[idx]:
            st.markdown(_card_html(candidate, is_showcase=True), unsafe_allow_html=True)
            if st.button("🔍 Kurulumu Aç", width="stretch", key=f"traj_showcase_{key_prefix}_{candidate['sym']}_{idx}"):
                open_detail(candidate)
                
    # 📋 2. DİĞER ADAYLAR BÖLÜMÜ: Kalanlar Temiz Kompakt Tablo / Grid Olarak Gösterilir
    remaining = candidates[showcase_count:]
    if remaining:
        st.markdown(f"<div style='font-size:0.78rem;font-weight:800;color:#94a3b8;margin:12px 0 6px 2px;'>"
                    f"📋 DİĞER TAKİP ADAYLARI ({len(remaining)} Hisse)</div>", unsafe_allow_html=True)
        
        # İlk 8 tanesini 4 kolonlu kompakt kutularda göster
        compact_preview = remaining[:8]
        for start in range(0, len(compact_preview), 4):
            row = compact_preview[start:start + 4]
            cols_c = st.columns(4)
            for offset, candidate in enumerate(row):
                with cols_c[offset]:
                    st.markdown(_card_html(candidate, is_showcase=False), unsafe_allow_html=True)
                    if st.button("🔍 İncele", width="stretch", key=f"traj_comp_{key_prefix}_{candidate['sym']}_{start+offset}"):
                        open_detail(candidate)
                        
        # 8'den fazlası varsa temiz bir açılır liste içinde ver
        if len(remaining) > 8:
            with st.expander(f"🔽 Tüm Kalan Adayları Gör (+{len(remaining) - 8} hisse)"):
                more_items = remaining[8:]
                for start in range(0, len(more_items), 4):
                    row = more_items[start:start + 4]
                    cols_m = st.columns(4)
                    for offset, candidate in enumerate(row):
                        with cols_m[offset]:
                            st.markdown(_card_html(candidate, is_showcase=False), unsafe_allow_html=True)
                            if st.button("🔍 İncele", width="stretch", key=f"traj_more_{key_prefix}_{candidate['sym']}_{start+offset}"):
                                open_detail(candidate)


def _render_header(st: Any, bucket: str, count: int) -> None:
    meta = BUCKET_META[bucket]
    st.markdown(
        f"<div style='font-size:0.92rem;font-weight:900;color:{meta['color']};margin:10px 0 2px 0;'>"
        f"{meta['title']} · {count} Aday</div>"
        f"<div style='font-size:0.68rem;color:#64748b;margin:0 0 8px 2px;'>{meta['note']}</div>",
        unsafe_allow_html=True,
    )


def _load_karne_summary() -> dict[str, Any]:
    if not KARNE_PATH.exists():
        return {}
    try:
        import json
        value = json.loads(KARNE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _render_live_karne(st: Any) -> None:
    """Skoru değiştirmeyen, seçilen grubun gerçekten fark yaratıp yaratmadığını gösteren sicil."""
    summary = _load_karne_summary()
    mature = summary.get("mature_counts", {}) if summary else {}
    ready = (summary.get("ready_vs_all_20", {}) or {}).get("karar_hazir", [])
    baseline = (summary.get("ready_vs_all_20", {}) or {}).get("tum_havuz", [])
    missed_tail = summary.get("missed_right_tail_20", {}) or {}
    with st.expander("📏 Canlı Ölçüm Karnesi (Seçilen Grup Fark Yaratıyor mu?)", expanded=False):
        st.caption("Bu tablo tarama seçmez ve puan değiştirmez. T+3'te öne çıkanları bütün T0 havuzuyla, yalnızca karar SONRASI sonuçlarda karşılaştırır.")
        st.markdown(
            f"Olgunlaşan olay: **5g {mature.get('5', 0)}** · **10g {mature.get('10', 0)}** · **20g {mature.get('20', 0)}**"
        )
        if not ready or not baseline:
            st.info("Henüz 20 günlük canlı sonuç oluşmadı. Eşik ve ağırlıklar değiştirilmeden veri biriktiriliyor.")
            return
        left, right = st.columns(2)
        ready_row, base_row = ready[0], baseline[0]
        with left:
            st.markdown("**T+3 karar hazır**")
            st.markdown(
                f"N: {ready_row.get('n', 0)} · Win rate: %{float(ready_row.get('win_rate') or 0) * 100:.1f} · "
                f"%30+ kuyruk: %{float(ready_row.get('sag_kuyruk_30') or 0) * 100:.1f}\n\n"
                f"Ort. getiri: %{float(ready_row.get('ortalama_getiri') or 0):.1f} · "
                f"BIST100 farkı: %{float(ready_row.get('bist100_alpha') or 0):.1f}"
            )
        with right:
            st.markdown("**Tüm T0 havuzu**")
            st.markdown(
                f"N: {base_row.get('n', 0)} · Win rate: %{float(base_row.get('win_rate') or 0) * 100:.1f} · "
                f"%30+ kuyruk: %{float(base_row.get('sag_kuyruk_30') or 0) * 100:.1f}\n\n"
                f"Ort. getiri: %{float(base_row.get('ortalama_getiri') or 0):.1f} · "
                f"BIST100 farkı: %{float(base_row.get('bist100_alpha') or 0):.1f}"
            )
        if ready_row.get("olgunluk") != "ölçülebilir" or base_row.get("olgunluk") != "ölçülebilir":
            st.warning("Örneklem henüz küçük. Bu ekran öğrenme sicilidir; kural değiştirme kanıtı değildir.")


def render_trajectory_tarama_merkezi(session_getter: Any, validate_fn: Any, on_click: Any) -> None:
    """Streamlit render. Yeni hesap yapmaz; kapanış collector çıktısını şık sekmeli düzende gösterir."""
    import streamlit as st

    payload = session_getter("toplu_terazi_data")
    ok, message = validate_fn(payload)
    if not ok:
        st.info(f"⏳ {message}")
        return

    _session_dates = _session_dates_from_payload(payload)
    if _session_dates is None:
        for _session_key in ("price_session_dates", "session_dates", "bist_session_dates"):
            try:
                _session_dates = _session_dates_from_payload({_session_key: session_getter(_session_key)})
            except Exception:
                _session_dates = None
            if _session_dates:
                break
    desk = build_trajectory_desk(payload, session_dates=_session_dates)
    catalog = tarama_merkezi.build_catalog(session_getter)
    counts = desk["counts"]
    as_of = desk.get("as_of") or str(payload.get("as_of") or "")[:10]
    
    st.markdown(
        "<div style='display:flex;justify-content:space-between;align-items:center;margin:6px 0 2px 0;'>"
        "<span style='font-size:1.10rem;font-weight:900;color:#38bdf8;'>🧭 Tarama Merkezi & Karar Masası</span>"
        f"<span style='font-size:0.72rem;color:#94a3b8;'>📅 {html.escape(as_of)} Kapanış Fotoğrafı</span>"
        "</div>",
        unsafe_allow_html=True,
    )
    if desk["catalog_only"]:
        st.caption("Kapanış takip fotoğrafı henüz oluşmadı; bu görünüm yalnızca T0 aday havuzudur.")
    _render_live_karne(st)

    @st.dialog("🔍 Kurulum Detayı", width="large")
    def open_detail(candidate: dict[str, Any]) -> None:
        meta = BUCKET_META[candidate["bucket"]]
        _masa = str(candidate.get("vade_masasi") or "KATALOG")
        _vade = str(candidate.get("vade") or "T+20")
        _expiry = str(candidate.get("son_kullanma_tarihi") or "hesaplanamadı")[:10]
        _karne = str(candidate.get("gecmis_karne") or "BİLMİYORUZ · ölçüm kaydı yok")
        st.markdown(
            f"<div style='font-size:0.68rem;font-weight:800;color:{meta['color']};'>{meta['tag']} · DETAY</div>"
            f"<div style='font-size:1.25rem;font-weight:900;'>{html.escape(candidate['sym'])} — {html.escape(candidate['story'])}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"**Vade / masa:** {html.escape(_masa)} · {html.escape(_vade)}  \n"
            f"**Son kullanma:** {html.escape(_expiry)}  \n"
            f"**Geçmiş karne:** {html.escape(_karne)}"
        )
        st.markdown("**Takip özeti**")
        st.markdown(
            f"- Olay başlangıcı: {candidate['event_start_date']} · T+{candidate['event_day']}\n"
            f"- Trajektori: {candidate['trajectory_v1']}/5 · çekirdek: {candidate['cur_core']}/3\n"
            f"- RSI hızı: {'olumlu' if candidate['rsi_up'] else 'henüz olumlu değil'}\n"
            f"- BIST100'e göre güç: {'olumlu' if candidate['rs_up'] else 'henüz olumlu değil'}\n"
            f"- Israr: {candidate['israr']} gün\n"
            f"- Kaynaklar: {', '.join(_display_scan(scan) for scan in candidate['scan_types']) or '—'}"
        )
        if candidate["crowded"]:
            st.warning(f"Kalabalık uyarısı: {candidate['scan_count']} taramada görünmüş. Bu bilgi puan değildir.")
        if candidate["bucket"] in (BUCKET_WATCH, BUCKET_RISK):
            st.warning(candidate["note"])
        if st.button("📊 Tam hisse analizini aç", width="stretch", key=f"trajectory_full_{candidate['sym']}_{candidate['event_start_date']}"):
            on_click(candidate["ticker"])
            st.session_state["_tm_scroll_top"] = True
            st.rerun(scope="app")

    # 🗂️ ŞIK SEKMELİ YAPI (Dikey sonsuz kaydırmayı bitirir)
    tab_ready, tab_growing, tab_new, tab_watch, tab_risk = st.tabs([
        f"🎯 Karar Hazır ({counts[BUCKET_READY]})",
        f"📈 Güçlenenler ({counts[BUCKET_GROWING]})",
        f"🔎 Yeni Adaylar ({counts[BUCKET_NEW]})",
        f"↔️ İzleme Masası ({counts[BUCKET_WATCH]})",
        f"⚠️ Risk Masası ({counts[BUCKET_RISK]})",
    ])

    bucket_tabs = [
        (tab_ready, BUCKET_READY),
        (tab_growing, BUCKET_GROWING),
        (tab_new, BUCKET_NEW),
        (tab_watch, BUCKET_WATCH),
        (tab_risk, BUCKET_RISK),
    ]

    for tab, bucket in bucket_tabs:
        with tab:
            _render_header(st, bucket, counts[bucket])
            _groups = group_candidates_by_vade(desk[bucket])
            for masa in _VADE_MASA_ORDER:
                _values = _groups[masa]
                if not _values:
                    continue
                st.markdown(
                    f"<div style='font-size:0.78rem;font-weight:800;color:#cbd5e1;"
                    f"margin:8px 0 3px 2px;'>{_VADE_MASA_LABELS[masa]} · {len(_values)}</div>",
                    unsafe_allow_html=True,
                )
                _render_grid(
                    st, _values, open_detail,
                    key_prefix=f"{bucket}_{masa.lower()}",
                )
            if not desk[bucket]:
                empty_text = {
                    BUCKET_READY: "Henüz T+3 karar kontrolünü geçen aday yok.",
                    BUCKET_GROWING: "Takipte güçlenen aday yok.",
                    BUCKET_NEW: "Yeni aday yok.",
                    BUCKET_WATCH: "İzleme gerektiren aday yok.",
                    BUCKET_RISK: "Sert risk vetosu alan hisse yok.",
                }[bucket]
                st.markdown(
                    "<div style='border:1px dashed #334155;border-radius:6px;padding:12px;text-align:center;"
                    f"color:#94a3b8;font-size:0.75rem;margin:10px 0;'>{empty_text}</div>",
                    unsafe_allow_html=True,
                )

    st.markdown(
        "<div style='font-size:0.92rem;font-weight:900;color:#e2e8f0;margin:16px 0 2px 0;'>📚 Tarama Kataloğu</div>"
        "<div style='font-size:0.66rem;color:#64748b;margin:0 0 6px 2px;'>Ham sonuçlar denetim alanıdır; karar listesine puan taşımaz.</div>",
        unsafe_allow_html=True,
    )
    for category in catalog:
        if not category["count"]:
            continue
        with st.expander(f"{category['name']} · {category['count']} sonuç · {category['family']}"):
            for index, symbol in enumerate(category["symbols"]):
                if st.button(symbol, key=f"trajectory_catalog_{category['key']}_{symbol}_{index}", width="stretch"):
                    on_click(symbol + ".IS" if "." not in symbol else symbol)
                    st.rerun(scope="app")
