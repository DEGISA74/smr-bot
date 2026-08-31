# -*- coding: utf-8 -*-
"""
trajectory_tarama_merkezi.py — Tarama Merkezi'nin T+3 yolculuk katmanı.

Bu modül, Master Scan'in bulduğu ham adayları "en çok taramada çıkan" diye
puanlamaz. `gelişmiş tarama/trajectory_forward_snapshots.csv` içindeki kapanış
fotoğraflarını okuyup adayın sonraki günlerdeki davranışını sınıflandırır:

    T0              → Bugün yeni yakalanan aday
    T+1 / T+2        → Takipte güçlenen aday
    T+3              → T+3 teyitli aday

Kalabalıklaşma yalnızca uyarıdır; sıralama puanı değildir. Sert risk vetosu ve
boğa-ayı çelişkisi, mevcut Kanıt Terazisi çıktısından okunur. Bu modül patron.db,
ana parquet'ler veya uygulama cache'lerine yazmaz; salt-okurdur.
"""
from __future__ import annotations

import html
import json
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
SCAN_KARNE_PATH = ROOT / "logs" / "tarama_karne.json"
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


_SCAN_KARNE_CACHE: dict[str, Any] = {
    "loaded": False,
    "mtime": None,
    "rows": [],
    "sorun": None,
}


def _load_scan_karne_rows() -> list[dict[str, Any]]:
    """Mühürlü tarama×vade geçmiş ölçümünü kartların okuyacağı satırlara çevirir."""
    try:
        mtime = SCAN_KARNE_PATH.stat().st_mtime_ns
    except OSError:
        mtime = None
    if _SCAN_KARNE_CACHE.get("loaded") and _SCAN_KARNE_CACHE.get("mtime") == mtime:
        return _SCAN_KARNE_CACHE.get("rows", [])

    clean_rows: list[dict[str, Any]] = []
    sorun: str | None = None
    try:
        import tarama_karne
    except Exception:
        # Tüketici kapısı yoksa ekranı düşürme; eski salt-okur davranışı korunur.
        try:
            package = json.loads(SCAN_KARNE_PATH.read_text(encoding="utf-8"))
            rows = package.get("kayitlar", []) if isinstance(package, dict) else []
            clean_rows = [row for row in rows if isinstance(row, dict)]
        except (OSError, ValueError, TypeError):
            clean_rows = []
    else:
        try:
            rows, sorun = tarama_karne.karne_oku(SCAN_KARNE_PATH, azami_gun=7)
            clean_rows = [row for row in (rows or []) if isinstance(row, dict)]
        except Exception as exc:
            clean_rows = []
            sorun = f"Tarama karnesi okunamadı: {type(exc).__name__}: {exc}"
    _SCAN_KARNE_CACHE["loaded"] = True
    _SCAN_KARNE_CACHE["mtime"] = mtime
    _SCAN_KARNE_CACHE["rows"] = clean_rows
    _SCAN_KARNE_CACHE["sorun"] = sorun
    return clean_rows


def _scan_karne_issue() -> str | None:
    """Karne kapısının sorununu kartların üstünde görünür kılmak için döndürür."""
    _load_scan_karne_rows()
    issue = _SCAN_KARNE_CACHE.get("sorun")
    return str(issue) if issue else None


def _fmt_metric(value: object) -> str:
    try:
        return f"{float(value):+.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "—"


def _history_karne(candidate: dict[str, Any], horizon: int = 5) -> tuple[str, str, str]:
    """Kart için vade geçmişini seçer: değer, durum etiketi ve durum rengi."""
    source_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in _card_scan_types(candidate):
        key = _canonical_scan_type(source)
        for row in _load_scan_karne_rows():
            if str(row.get("tarama") or "") != key or str(row.get("vade") or "") != f"T+{horizon}":
                continue
            if key not in seen:
                source_rows.append({"source": source, **row})
                seen.add(key)
            break
    if not source_rows:
        fallback = str(candidate.get("gecmis_karne") or "BİLMİYORUZ · ölçüm kaydı yok")
        return fallback, "BİLMİYORUZ", "#fbbf24"

    rendered = []
    statuses = []
    for row in source_rows:
        source = html.escape(_display_scan(row.get("source")))
        rendered.append(
            f"{source}: ↑ {_fmt_metric(row.get('yukselen_taban_farki'))} "
            f"(N={row.get('yukselen_n', '—')}) · ↓ {_fmt_metric(row.get('dusen_taban_farki'))} "
            f"(N={row.get('dusen_n', '—')})"
        )
        statuses.append(str(row.get("durum") or "BELİRSİZ").replace("_", " "))
    status = " / ".join(dict.fromkeys(statuses))
    if all(item.get("durum") == "KANITLI_TABAN_USTU" for item in source_rows):
        color = "#4ade80"
    elif any(item.get("durum") == "EVREN_TABANI_ALTI" for item in source_rows):
        color = "#f87171"
    else:
        color = "#fbbf24"
    return "<br>".join(rendered), status, color


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
    suppressed = []
    expired = []
    for record in records:
        expiry = record.get("son_kullanma_tarihi")
        # Son gün dahildir; sonraki kapanışta sinyal kapanır.
        if expiry and as_of_day and as_of_day > str(expiry)[:10]:
            expired.append(record)
        elif evidence.is_ai_suppressed(str(record.get("key") or "").strip()):
            suppressed.append(record)
        else:
            active.append(record)
    # Kaynağın tamamı susturulduysa kaynak satırını boş bırakıp iddialı genel
    # hikâye üretmek yerine aday, süresi dolmuş sinyal gibi karar masasından
    # çıkarılır. Karışık adaylarda yalnız görünür ve süresi dolmamış kaynaklar
    # korunur; masa seçimi hâlâ bu görünür kaynakların vade sırasındadır.
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
        "vade_kayitlari": active,
        "suresi_dolmus_kaynaklar": [record.get("key") for record in expired],
        "susturulmus_kaynaklar": [record.get("key") for record in suppressed],
    })
    return candidate


def _card_scan_types(candidate: dict[str, Any]) -> list[str]:
    """Kartta yalnız susturulmamış kaynakları gösterir.

    Master Scan ham üyelikleri ölçüm için korunur. KISA kartında KATALOG'a
    alınmış ve AI/karar yüzeyinden susturulmuş kaynakların yazılması, emekli
    taramayı kısa vade kurulumu gibi gösteriyordu. Masa fark etmeksizin
    susturulmamış Radar2/Liderlik kaynakları korunur.
    """
    raw_types = [
        str(value).strip()
        for value in (candidate.get("scan_types") or [])
        if str(value).strip()
    ]
    records = candidate.get("vade_kayitlari") or []
    if records:
        visible = [
            str(record.get("raw") or record.get("key") or "").strip()
            for record in records
            if not evidence.is_ai_suppressed(str(record.get("key") or "").strip())
        ]
    else:
        visible = [
            value for value in raw_types
            if not evidence.is_ai_suppressed(_canonical_scan_type(value))
        ]
    visible = [value for value in visible if value]
    return visible


def _card_story(candidate: dict[str, Any]) -> str:
    """Kart hikâyesini görünür, susturulmamış kaynaklardan kurar."""
    story = str(candidate.get("story") or "📊 Algoritmik Güçlü Kurulum")
    if not candidate.get("vade_kayitlari"):
        return ""
    visible_sources = _card_scan_types(candidate)
    if not visible_sources:
        return ""
    visible_story = _story_for_scans(visible_sources)
    return visible_story or "📊 Algoritmik Güçlü Kurulum"


BUCKET_READY = "karar_hazir"
BUCKET_GROWING = "guclenen"
BUCKET_NEW = "yeni_aday"
BUCKET_WATCH = "izleme"
BUCKET_RISK = "risk"

BUCKETS = (BUCKET_READY, BUCKET_GROWING, BUCKET_NEW, BUCKET_WATCH, BUCKET_RISK)

BUCKET_META = {
    BUCKET_READY: {
        "title": "🎯 T+3 Teyitli Adaylar",
        "note": "T+3 tamamlandı; fiyat davranışı güç sinyalini korudu.",
        "color": "#22c55e",
        "tag": "T+3 TEYİTLİ",
    },
    BUCKET_GROWING: {
        "title": "📈 T+1 & T+2 Onaylılar",
        "note": "T+1 / T+2: fiyat davranışı olumlu ve güçlenme koşulu onaylandı.",
        "color": "#f59e0b",
        "tag": "T+1 & T+2 ONAYLI",
    },
    BUCKET_NEW: {
        "title": "🔎 Yeni Sinyal T+0",
        "note": "T0: takip başladı. İlk gün hiçbir hisseye güçlü LONG etiketi verilmez.",
        "color": "#38bdf8",
        "tag": "YENİ SİNYAL T+0",
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


def _flow_alignment(candidate: dict[str, Any]) -> dict[str, str] | None:
    """Mevcut Toplu Terazi oylarından CMF + OBV uyumunu okur.

    Yeni gösterge hesabı yapmaz. Aynı kapanış fotoğrafında üretilmiş ölçülmüş
    ``Para akışı`` ve ``OBV birikim izi`` oyları birlikte varsa, kartta yalnızca
    bilgi rozeti göstermek için küçük bir açıklama döndürür. Tek bir oy veya
    ölçülmemiş oy, rozet için yeterli sayılmaz.
    """
    item = candidate.get("payload_item") or {}
    result = item.get("result") or {}
    terazi = result.get("terazi") or {}
    votes = terazi.get("votes") or []
    cmf_positive = False
    obv_positive = False
    for vote in votes:
        if not isinstance(vote, dict):
            continue
        if vote.get("yon") != "boga" or not vote.get("olculmus"):
            continue
        name = str(vote.get("ad") or "").strip().lower()
        if "para akışı" in name and "pozitif" in name:
            cmf_positive = True
        if name == "obv birikim izi":
            obv_positive = True
    if not (cmf_positive and obv_positive):
        return None
    return {
        "label": "💧 CMF + OBV olumlu",
        "detail": "CMF20 pozitif · OBV yönü pozitif",
        "title": "Aynı kapanış fotoğrafında ölçülmüş CMF20 ve OBV olumlu. Bilgi rozetidir; puan veya eylem hükmü değildir.",
    }


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
                enriched["flow_alignment"] = _flow_alignment(enriched)
                active_candidates.append(enriched)
        out[bucket] = _sort_candidates(bucket, active_candidates)
    out["counts"] = {bucket: len(out[bucket]) for bucket in BUCKETS}
    return out


def _card_html(candidate: dict[str, Any], *, is_showcase: bool = False) -> str:
    meta = BUCKET_META[candidate["bucket"]]
    symbol = html.escape(candidate["sym"])
    story = html.escape(_card_story(candidate))
    note = html.escape(candidate["note"])
    visible_sources = _card_scan_types(candidate)
    source_text = " · ".join(_display_scan(x) for x in visible_sources) or "—"
    source_text = html.escape(source_text)
    day = candidate["event_day"]
    current = "T0 (Yeni)" if day == 0 else f"T+{day} (Takipte)"
    masa = str(candidate.get("vade_masasi") or "KATALOG")
    masa_label = html.escape(_VADE_MASA_LABELS.get(masa, masa))
    vade = html.escape(str(candidate.get("vade") or "T+20"))
    expiry = candidate.get("son_kullanma_tarihi")
    expiry_text = html.escape(str(expiry)[:10] if expiry else "hesaplanamadı")

    if candidate["bucket"] == BUCKET_READY:
        _ready_day = f"T+{day} kontrolü tamamlandı"
        if candidate.get("trajectory_v1", 0) >= 3:
            _strength = f"Güç teyidi {candidate.get('trajectory_v1', 0)}/5"
        else:
            _strength = f"Çekirdek güç teyidi {candidate.get('cur_core', 0)}/3"
        reason_text = f"{_ready_day} · {_strength} · risk vetosu yok"
    elif candidate["bucket"] == BUCKET_GROWING:
        reason_text = f"{current} · önceki kapanışa göre güçlendi"
    elif candidate["bucket"] == BUCKET_NEW:
        reason_text = "T0 · ilk gün aday, takip başlıyor"
    else:
        reason_text = str(candidate.get("note") or "İzleme koşulu oluştu")

    history_text, history_status, history_color = _history_karne(candidate, horizon=5)
    if history_status == "KANITLI TABAN USTU":
        history_badge = "✅ Kanıtlı"
    elif "EVREN TABANI ALTI" in history_status:
        history_badge = "⚠️ Evren tabanının altında"
    elif history_status == "BİLMİYORUZ":
        history_badge = "⚪ Ölçüm yok"
    else:
        history_badge = "⚪ Belirsiz örneklem"

    info_html = (
        "<div style='display:grid;grid-template-columns:1.15fr 0.85fr;gap:8px;"
        "margin:10px 0 0 0;'>"
        "<div style='background:#0b1220;border:1px solid #1e3a5f;border-radius:6px;"
        "padding:7px 8px;min-height:58px;'>"
        "<div style='font-size:0.61rem;font-weight:900;color:#38bdf8;margin-bottom:4px;'>"
        "NEDEN BU LİSTEDE?</div>"
        f"<div style='font-size:0.69rem;color:#e2e8f0;line-height:1.35;'>{html.escape(reason_text)}</div>"
        "</div>"
        "<div style='background:#0b1220;border:1px solid #1e3a5f;border-radius:6px;"
        "padding:7px 8px;min-height:58px;'>"
        "<div style='font-size:0.61rem;font-weight:900;color:#38bdf8;margin-bottom:4px;'>"
        "GEÇTİĞİ TARAMALAR</div>"
        f"<div style='font-size:0.69rem;color:#e2e8f0;line-height:1.35;'>{source_text}</div>"
        "</div></div>"
    )
    history_html = (
        "<div style='background:#0b1220;border:1px solid #1e3a5f;border-radius:6px;"
        "padding:7px 8px;margin:8px 0 0 0;'>"
        "<div style='font-size:0.61rem;font-weight:900;color:#38bdf8;margin-bottom:4px;'>"
        "GEÇMİŞ KARNE · T+5 · XU100 / EVREN FARKI</div>"
        f"<div style='font-size:0.68rem;color:#e2e8f0;line-height:1.4;'>{history_text}</div>"
        f"<div style='font-size:0.64rem;color:{history_color};font-weight:800;margin-top:4px;'>"
        f"{history_badge}</div></div>"
    )
    vade_html = (
        "<div style='display:grid;grid-template-columns:1.15fr 0.85fr;gap:8px;"
        "border-top:1px solid #334155;padding-top:7px;margin-top:8px;'>"
        f"<div><div style='font-size:0.60rem;color:#94a3b8;'>Vade</div>"
        f"<div style='font-size:0.69rem;color:#e2e8f0;margin-top:2px;'>{masa_label} · {vade}</div></div>"
        f"<div><div style='font-size:0.60rem;color:#94a3b8;'>Son kullanma</div>"
        f"<div style='font-size:0.69rem;color:#e2e8f0;margin-top:2px;'>{expiry_text}</div></div>"
        "</div>"
    )

    # Hazır/güçlenen kartlarda güç zaten 'Neden bu listede' satırında görünür;
    # aynı rozeti ikinci kez basmayarak kartın yüksekliğini koruruz.
    badges = []
    is_ext = "aşırı uzamış" in note.lower() or "fomo" in note.lower()
    if is_ext:
        badges.append("<span style='background:#ef44441a;color:#f87171;border:1px solid #ef444444;padding:3px 7px;border-radius:4px;font-size:0.66rem;font-weight:800;'>⚠️ AŞIRI UZAMIŞ · FOMO RİSKİ</span>")
    elif candidate["bucket"] == BUCKET_NEW:
        badges.append("<span style='background:#38bdf81a;color:#38bdf8;border:1px solid #38bdf844;padding:3px 7px;border-radius:4px;font-size:0.66rem;font-weight:700;'>🔎 T0 · İLK GÜN SİNYALİ</span>")
    elif candidate["bucket"] not in (BUCKET_READY, BUCKET_GROWING):
        badges.append(f"<span style='background:#f59e0b1a;color:#fbbf24;border:1px solid #f59e0b44;padding:3px 7px;border-radius:4px;font-size:0.66rem;font-weight:700;'>↔️ {meta['tag']}</span>")

    flow = candidate.get("flow_alignment")
    if flow:
        flow_title = str(flow.get("title") or "")
        flow_detail = str(flow.get("detail") or "")
        flow_tip = html.escape(
            " · ".join(part for part in (flow_title, flow_detail) if part),
            quote=True,
        )
        badges.append(
            f"<span title='{flow_tip}' "
            "style='background:#06b6d41a;color:#67e8f9;border:1px solid #06b6d466;"
            "padding:3px 7px;border-radius:4px;font-size:0.66rem;font-weight:800;cursor:help;'>"
            f"{html.escape(str(flow.get('label') or '💧 CMF + OBV olumlu'))}</span>"
        )

    badge_html = "".join(badges)
    crowded = ""
    if candidate.get("crowded"):
        crowded = (
            "<div style='color:#fbbf24;font-size:0.64rem;margin-top:5px;'>"
            f"⚠ Kalabalık: {candidate['scan_count']} taramada çıktı (güç puanı değildir)</div>"
        )

    return (
        f"<div style='padding:9px 10px 7px 10px;'>"
        f"<div style='height:3px;background:{meta['color']};border-radius:2px;margin-bottom:9px;'></div>"
        "<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:8px;'>"
        f"<span style='font-weight:900;font-size:1.28rem;color:#f8fafc;letter-spacing:0.4px;'>{symbol}</span>"
        f"<span style='font-size:0.66rem;font-weight:800;color:{meta['color']};background:{meta['color']}18;"
        f"border:1px solid {meta['color']}55;border-radius:4px;padding:4px 6px;text-align:right;'>{meta['tag']}<br>{current}</span>"
        "</div>"
        f"<div style='font-size:0.88rem;font-weight:800;color:#7dd3fc;line-height:1.25;margin:9px 0 0 0;'>{story}</div>"
        f"{info_html}"
        f"{history_html}"
        f"{vade_html}"
        f"<div style='margin:7px 0 0 0;'>{badge_html}</div>"
        f"{crowded}"
        "</div>"
    )


def _render_grid(st: Any, candidates: list[dict[str, Any]], open_detail: Any, *, key_prefix: str) -> None:
    if not candidates:
        return

    def _render_rows(items: list[dict[str, Any]], row_prefix: str) -> None:
        for start in range(0, len(items), 3):
            row = items[start:start + 3]
            cols = st.columns(3)
            for offset, candidate in enumerate(row):
                with cols[offset]:
                    with st.container(border=True):
                        st.markdown(_card_html(candidate, is_showcase=True), unsafe_allow_html=True)
                        if st.button(
                            "🔍 Kurulumu Aç",
                            width="stretch",
                            key=f"traj_card_{row_prefix}_{start + offset}_{candidate['sym']}",
                        ):
                            open_detail(candidate)

    # İlk 9 kart aynı seviyede görünür; büyük listeler tek bir açılır alanda tutulur.
    visible = candidates[:9]
    _render_rows(visible, key_prefix)
    remaining = candidates[9:]
    if remaining:
        with st.expander(f"🔽 Kalan {len(remaining)} adayı göster"):
            _render_rows(remaining, f"{key_prefix}_remaining")


def _order_candidates_by_liquidity(
    candidates: list[dict[str, Any]],
    *,
    as_of: object = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Adayları mevcut 20 günlük TL likidite sırasına dizer."""
    values = list(candidates or [])
    if not values:
        return [], False
    symbols = [str(candidate.get("sym") or "") for candidate in values]
    ordered_symbols, measured = _liquidity_top(
        symbols,
        as_of=as_of,
        limit=len(values),
    )
    by_symbol = {str(candidate.get("sym") or ""): candidate for candidate in values}
    ordered = []
    seen = set()
    for symbol in ordered_symbols:
        candidate = by_symbol.get(symbol)
        if candidate is not None:
            ordered.append(candidate)
            seen.add(symbol)
    ordered.extend(candidate for candidate in values if candidate.get("sym") not in seen)
    return ordered, measured


def _compact_reason(candidate: dict[str, Any]) -> str:
    bucket = candidate.get("bucket")
    day = int(candidate.get("event_day") or 0)
    if bucket == BUCKET_READY:
        return f"T+{day} tamamlandı · güç {candidate.get('trajectory_v1', 0)}/5 · çekirdek {candidate.get('cur_core', 0)}/3"
    if bucket == BUCKET_GROWING:
        return f"T+{day} · önceki kapanışa göre güçlendi"
    return str(candidate.get("note") or "İzleme koşulu oluştu")


def _compact_history_status(candidate: dict[str, Any]) -> tuple[str, str]:
    _history_text, history_status, history_color = _history_karne(candidate, horizon=5)
    if history_status == "KANITLI TABAN USTU":
        return "✅ Kanıtlı", history_color
    if "EVREN TABANI ALTI" in history_status:
        return "⚠️ Evren altı", history_color
    if history_status == "BİLMİYORUZ":
        return "⚪ Ölçüm yok", history_color
    return "⚪ Belirsiz", history_color


def _render_compact_rows(
    st: Any,
    candidates: list[dict[str, Any]],
    open_detail: Any,
    *,
    key_prefix: str,
) -> None:
    """Büyük kart yerine taranabilir, tek satırlık aday listesi çizer."""
    for index, candidate in enumerate(candidates or []):
        sources = " · ".join(_display_scan(source) for source in _card_scan_types(candidate)[:2]) or "Kaynak yok"
        story = _card_story(candidate) or "Algoritmik kurulum"
        reason = _compact_reason(candidate)
        if len(reason) > 78:
            reason = reason[:75] + "..."
        history_badge, history_color = _compact_history_status(candidate)
        with st.container(border=True):
            cols = st.columns([0.72, 1.65, 1.85, 1.30, 0.52])
            with cols[0]:
                st.markdown(
                    f"<div style='font-size:0.94rem;font-weight:900;color:#f8fafc;'>"
                    f"{html.escape(str(candidate.get('sym') or '—'))}</div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                _flow_html = ""
                if candidate.get("flow_alignment"):
                    _flow_html = (
                        "<div title='CMF20 pozitif · OBV yönü pozitif; bilgi rozetidir.' "
                        "style='font-size:0.58rem;color:#67e8f9;font-weight:800;margin-top:3px;'>"
                        "💧 CMF + OBV olumlu</div>"
                    )
                st.markdown(
                    f"<div style='font-size:0.68rem;color:#7dd3fc;font-weight:800;line-height:1.2;'>"
                    f"{html.escape(story)}</div>"
                    f"<div style='font-size:0.58rem;color:#64748b;margin-top:3px;'>"
                    f"{html.escape(sources)}</div>{_flow_html}",
                    unsafe_allow_html=True,
                )
            with cols[2]:
                st.markdown(
                    f"<div style='font-size:0.63rem;color:#cbd5e1;line-height:1.25;'>"
                    f"{html.escape(reason)}</div>",
                    unsafe_allow_html=True,
                )
            with cols[3]:
                st.markdown(
                    f"<div style='font-size:0.58rem;color:#64748b;'>GEÇMİŞ T+5</div>"
                    f"<div style='font-size:0.64rem;color:{history_color};font-weight:800;margin-top:2px;'>"
                    f"{html.escape(history_badge)}</div>",
                    unsafe_allow_html=True,
                )
            with cols[4]:
                if st.button(
                    "Aç",
                    key=f"trajectory_compact_{key_prefix}_{index}_{candidate['sym']}",
                    width="stretch",
                ):
                    open_detail(candidate)


def render_standard_scan_list(
    st: Any,
    items: list[dict[str, Any]],
    open_detail: Any,
    *,
    key_prefix: str,
    priority_title: str = "🔎 İLK BAKILACAK 5",
    priority_note: str = "Mevcut taramanın sırası korunur; bu bölüm tek başına alım teyidi değildir.",
    priority_color: str = "#4ade80",
    empty_text: str = "Bu taramada sonuç yok.",
) -> None:
    """Aday üreten farklı taramaları aynı, dar liste hiyerarşisinde gösterir.

    Bu yardımcı puan hesaplamaz ve sıralama yapmaz. Çağıran panelin ürettiği
    sıralamayı korur; yalnızca ilk beşi görünür öncelik, geri kalanı kompakt
    takip listesi ve açılır ham sonuçlar olarak çizer.
    """
    clean_items = [item for item in (items or []) if isinstance(item, dict)]
    if not clean_items:
        st.markdown(
            f"<div style='border:1px dashed {priority_color}55;border-radius:7px;"
            f"padding:14px 10px;text-align:center;color:#94a3b8;font-size:0.76rem;'>"
            f"{html.escape(empty_text)}</div>",
            unsafe_allow_html=True,
        )
        return

    priority = clean_items[:5]
    remaining = clean_items[5:]
    st.markdown(
        f"<div style='font-size:0.80rem;font-weight:900;color:{priority_color};"
        f"margin:8px 0 2px 0;'>{html.escape(priority_title)} · {len(clean_items)} sonuç</div>"
        f"<div style='font-size:0.64rem;color:#64748b;margin:0 0 6px 2px;'>"
        f"{html.escape(priority_note)}</div>",
        unsafe_allow_html=True,
    )

    def _draw_row(item: dict[str, Any], index: int, *, prominent: bool) -> None:
        symbol = str(item.get("symbol") or "—")
        label = str(item.get("label") or item.get("title") or "Algoritmik aday")
        detail = str(item.get("detail") or "")
        status = str(item.get("status") or "")
        button_text = f"{item.get('icon', '•')} {symbol}"
        if item.get("price") not in (None, "", "—"):
            button_text += f" · {item.get('price')}"
        if item.get("rank") not in (None, "", "—"):
            button_text += f" · {item.get('rank')}"
        with st.container(border=True):
            cols = st.columns([0.92, 2.55, 1.10])
            with cols[0]:
                st.markdown(
                    f"<div style='font-size:{'0.94' if prominent else '0.82'}rem;"
                    f"font-weight:900;color:#f8fafc;'>{html.escape(symbol)}</div>",
                    unsafe_allow_html=True,
                )
            with cols[1]:
                st.markdown(
                    f"<div style='font-size:{'0.71' if prominent else '0.64'}rem;"
                    f"color:#7dd3fc;font-weight:800;line-height:1.25;'>"
                    f"{html.escape(label)}</div>"
                    f"<div style='font-size:0.59rem;color:#cbd5e1;line-height:1.3;"
                    f"margin-top:3px;'>{html.escape(detail)}</div>"
                    + (f"<div style='font-size:0.57rem;color:#fbbf24;margin-top:2px;'>"
                       f"{html.escape(status)}</div>" if status else ""),
                    unsafe_allow_html=True,
                )
            with cols[2]:
                if st.button(
                    "Aç",
                    key=f"standard_scan_{key_prefix}_{index}_{symbol}",
                    width="stretch",
                ):
                    open_detail(item)

    for index, item in enumerate(priority):
        _draw_row(item, index, prominent=True)
    if remaining:
        with st.expander(f"🔽 Kalan {len(remaining)} sonucu kompakt göster"):
            for offset, item in enumerate(remaining, start=len(priority)):
                _draw_row(item, offset, prominent=False)


def _render_ready_priority(
    st: Any,
    candidates: list[dict[str, Any]],
    open_detail: Any,
    *,
    as_of: object = None,
) -> None:
    ordered, measured = _order_candidates_by_liquidity(candidates, as_of=as_of)
    priority = ordered[:5]
    remaining = ordered[5:]
    if not priority:
        return
    st.markdown(
        "<div style='font-size:0.82rem;font-weight:900;color:#4ade80;margin:8px 0 2px 0;'>"
        f"💧 İLK BAKILACAK {len(priority)} · {len(ordered)} T+3 teyitli aday içinden</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        "20 günlük ortalama TL işlem hacmine göre sıralandı."
        if measured
        else "Likidite ölçümü hazır değil; mevcut güç sırası korunuyor."
    )
    _render_grid(st, priority, open_detail, key_prefix="karar_hazir_likidite")
    if remaining:
        with st.expander(f"🔽 Kalan {len(remaining)} T+3 teyitli adayı kompakt göster"):
            _render_compact_rows(
                st,
                remaining,
                open_detail,
                key_prefix="karar_hazir_kalan",
            )


def _render_confirm_pool(
    st: Any,
    desk: dict[str, Any],
    open_detail: Any,
    *,
    as_of: object = None,
) -> None:
    """206'lık ara havuzu güçlenme ve izleme masalarına ayırır."""
    group_specs = (
        (
            BUCKET_GROWING,
            "📈 Takipte güçlenenler",
            "T+1/T+2'de güç kazananlar; T+3 karar kontrolü henüz tamamlanmadı.",
        ),
        (
            BUCKET_WATCH,
            "↔ İzleme / teyit eksik",
            "Çelişki, aşırı uzama veya güçlenme teyidi eksik olanlar; işlem listesi değildir.",
        ),
    )
    for bucket, title, note in group_specs:
        values, measured = _order_candidates_by_liquidity(
            desk.get(bucket, []),
            as_of=as_of,
        )
        st.markdown(
            f"<div style='font-size:0.80rem;font-weight:900;color:{BUCKET_META[bucket]['color']};"
            f"margin:12px 0 1px 0;'>{title} · {len(values)} aday</div>"
            f"<div style='font-size:0.65rem;color:#64748b;margin:0 0 6px 2px;'>{note}</div>",
            unsafe_allow_html=True,
        )
        if not values:
            st.caption("Bu alt grupta aday yok.")
            continue
        visible = values[:10]
        st.caption(
            "Sıra: 20 günlük ortalama TL işlem hacmi."
            if measured
            else "Likidite ölçümü hazır değil; mevcut takip sırası korunuyor."
        )
        _render_compact_rows(
            st,
            visible,
            open_detail,
            key_prefix=f"{bucket}_visible",
        )
        remaining = values[10:]
        if remaining:
            with st.expander(f"🔽 Kalan {len(remaining)} adayı kompakt göster"):
                _render_compact_rows(
                    st,
                    remaining,
                    open_detail,
                    key_prefix=f"{bucket}_remaining",
                )


def _render_header(st: Any, bucket: str, count: int) -> None:
    meta = BUCKET_META[bucket]
    st.markdown(
        f"<div style='font-size:0.92rem;font-weight:900;color:{meta['color']};margin:10px 0 2px 0;'>"
        f"{meta['title']} · {count} Aday</div>"
        f"<div style='font-size:0.68rem;color:#64748b;margin:0 0 8px 2px;'>{meta['note']}</div>",
        unsafe_allow_html=True,
    )


def _payload_sources(candidate: dict[str, Any]) -> set[str]:
    """Toplu Terazi adayının ölçümdeki ham kaynaklarını döndürür."""
    item = candidate.get("payload_item") or {}
    return {
        str(source).strip().lower()
        for source in (item.get("sources") or [])
        if str(source).strip()
    }


def _goldmine_candidates(desk: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Toplu Terazi payload'ındaki Gold Mine üyelerini tekilleştirir."""
    found: dict[str, dict[str, Any]] = {}
    for bucket in BUCKETS:
        for candidate in desk.get(bucket, []):
            if "goldmine" not in _payload_sources(candidate):
                continue
            symbol = _clean_symbol(candidate.get("sym"))
            if symbol:
                found.setdefault(symbol, candidate)
    return found


def _liquidity_top(
    symbols: list[str],
    *,
    as_of: object = None,
    scan_type: str | None = None,
    limit: int = 4,
) -> tuple[list[str], bool]:
    """Mevcut likidite kuralını kullanır; ölçüm yoksa 'en likit' iddiası kurmaz."""
    clean = list(dict.fromkeys(_clean_symbol(symbol) for symbol in (symbols or []) if _clean_symbol(symbol)))
    if not clean:
        return [], False
    try:
        import likidite_siralama as liquidity

        ordered = liquidity.sirala(
            clean,
            tarih=as_of,
            adet=limit,
            scan_type=scan_type,
        )
        if not ordered:
            return [], False
        measured = [
            symbol for symbol in ordered
            if pd.notna(liquidity.likidite(symbol, tarih=as_of))
        ]
        return ordered[:limit], bool(measured)
    except Exception:
        return [], False


def _render_goldmine_liquidity(
    st: Any,
    desk: dict[str, Any],
    on_click: Any,
) -> None:
    """Gold Mine'ın 28 Ağustos'ta ölçülen EN LİKİT 4 seçimini Katalog'a taşır."""
    goldmine = _goldmine_candidates(desk)
    if not goldmine:
        return
    as_of = desk.get("as_of")
    top, measured = _liquidity_top(
        list(goldmine),
        as_of=as_of,
        scan_type="goldmine",
    )
    st.markdown(
        "<div style='font-size:0.86rem;font-weight:900;color:#fbbf24;"
        "margin:4px 0 4px 0;'>💧 GOLD MINE · EN LİKİT 4</div>"
        "<div style='font-size:0.66rem;color:#94a3b8;margin:0 0 6px 2px;'>"
        "20 günlük ortalama TL işlem hacmiyle seçilir; puan veya 'en iyi' sırası değildir."
        "</div>",
        unsafe_allow_html=True,
    )
    if not measured:
        st.markdown(
            "<div style='border:1px dashed #f59e0b55;border-radius:7px;padding:10px;"
            "text-align:center;color:#94a3b8;font-size:0.72rem;'>"
            "Gold Mine üyeleri var; likidite ölçümü hazır olmadığı için sıralama gösterilmedi."
            "</div>",
            unsafe_allow_html=True,
        )
        return
    with st.container(border=True):
        for index, symbol in enumerate(top):
            candidate = goldmine.get(symbol) or {}
            story = _card_story(candidate) or "📊 Algoritmik Güçlü Kurulum"
            sources = " · ".join(
                "Gold Mine" if str(source).strip().lower() == "goldmine"
                else _display_scan(source)
                for source in _card_scan_types(candidate)[:3]
            ) or "Gold Mine"
            if st.button(
                f"💧 {index + 1}. {symbol} · {story}",
                key=f"trajectory_goldmine_{symbol}_{index}",
                width="stretch",
                help=f"EN LİKİT seçim · {sources}",
            ):
                on_click(candidate.get("ticker") or f"{symbol}.IS")
                st.rerun(scope="app")
            st.markdown(
                f"<div style='font-size:0.66rem;color:#94a3b8;margin:-6px 0 4px 8px;'>"
                f"{html.escape(sources)} · Gold Mine üyeliği · bağımsız karar puanı değil"
                "</div>",
                unsafe_allow_html=True,
            )


def _short_candidates(session_getter: Any) -> dict[str, list[str]]:
    """Erken Radar D4/D5 satırlarını hisse bazında toplar."""
    try:
        early = session_getter("erken_radar_data")
    except Exception:
        return {}
    if early is None or not hasattr(early, "empty") or early.empty:
        return {}
    if not {"Sembol", "ScenarioId"}.issubset(early.columns):
        return {}
    found: dict[str, list[str]] = {}
    short_rows = early[early["ScenarioId"].astype(str).isin(("D4", "D5"))]
    for _, row in short_rows.iterrows():
        symbol = _clean_symbol(row.get("Sembol"))
        scenario = str(row.get("ScenarioId") or "").strip()
        if symbol and scenario:
            found.setdefault(symbol, [])
            if scenario not in found[symbol]:
                found[symbol].append(scenario)
    return found


def _render_short_warning(
    st: Any,
    session_getter: Any,
    on_click: Any,
    *,
    as_of: object = None,
    show_empty: bool = False,
) -> None:
    """OLASI SHORT uyarısını ayrı görünümde, işlem aracına çevirmeden gösterir."""
    short_map = _short_candidates(session_getter)
    if not short_map:
        if show_empty:
            st.markdown(
                "<div style='background:linear-gradient(135deg,#7f1d1d22,#1c191706);"
                "border:1px solid #dc262655;border-radius:8px;padding:10px 12px;"
                "margin:2px 0 8px 0;'>"
                "<span style='font-size:0.82rem;font-weight:900;color:#fca5a5;'>"
                "📉 OLASI SHORT — 0 hisse</span>"
                "<div style='font-size:0.68rem;color:#d6d3d1;line-height:1.35;margin-top:4px;'>"
                "Erken Radar D4/D5 satırı yok. İlk gerçek liste, D4/D5 açık Master Scan sonrasında burada görünecek."
                "</div></div>",
                unsafe_allow_html=True,
            )
        return
    top, measured = _liquidity_top(
        list(short_map),
        as_of=as_of,
        scan_type="er_D4",
    )
    display_top = top if measured else list(short_map)[:4]
    short_order_note = (
        "en likit 4 üstte"
        if measured
        else "likidite ölçümü yok; kaynak sırası gösteriliyor"
    )
    st.markdown(
        "<div style='background:linear-gradient(135deg,#7f1d1d22,#1c191706);"
        "border:1px solid #dc262655;border-radius:8px;padding:7px 10px;"
        "margin:2px 0 8px 0;'>"
        "<span style='font-size:0.78rem;font-weight:900;color:#fca5a5;'>"
        f"📉 OLASI SHORT — {len(short_map)} hisse</span>"
        "<div style='font-size:0.63rem;color:#d6d3d1;line-height:1.3;margin-top:2px;'>"
        f"er_D4 / er_D5 düşüş uyarısı · {short_order_note}<br>"
        "<b>Uyarı listesidir, işlem tavsiyesi değil.</b> İşlem yapılabilir evrende "
        "(VİOP 47 / BIST 50) kenar ölçümü sınırlıdır."
        "</div></div>",
        unsafe_allow_html=True,
    )
    if not measured:
        st.caption("Likidite ölçümü hazır değil; EN LİKİT sırası gösterilmedi.")
    for index, symbol in enumerate(display_top):
        scenarios = " + ".join(sorted(short_map.get(symbol, [])))
        if st.button(
            f"💧 {index + 1}. {symbol} · {scenarios}",
            key=f"trajectory_short_{symbol}_{index}",
            width="stretch",
            help="D4/D5 uyarısı · işlem tavsiyesi değildir",
        ):
            on_click(symbol)
            st.rerun(scope="app")
    remaining = len(short_map) - len(display_top)
    if remaining > 0:
        st.markdown(
            "<div style='font-size:0.64rem;color:#94a3b8;margin:-3px 0 6px 6px;'>"
            f"+{remaining} hisse daha (EN LİKİT 4 dışında)"
            "</div>",
            unsafe_allow_html=True,
        )


def _cizgi_master_items(
    session_getter: Any,
    catalog: list[dict[str, Any]],
    *,
    as_of: object = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Çizgi Yapısı sonuçlarını ortak tarama kaynaklarıyla zenginleştirir."""
    try:
        raw_results = session_getter("cizgi_yapi_master_data")
    except Exception:
        raw_results = None
    if not isinstance(raw_results, list):
        return [], False

    items: list[dict[str, Any]] = []
    for row in raw_results:
        if not isinstance(row, dict):
            continue
        symbol = _clean_symbol(row.get("kisa") or row.get("sembol"))
        if not symbol:
            continue
        other_sources = [
            source
            for source in tarama_merkezi.candidate_scan_membership(symbol, catalog)
            if str(source).strip() and str(source).strip() != "Çizgi Yapısı"
        ]
        is_bist100 = bool(row.get("bist100"))
        try:
            price = f"{float(row.get('fiyat')):,.2f}"
        except Exception:
            price = "—"
        source_text = " · ".join(["Çizgi Yapısı", *other_sources])
        detail_parts = [
            f"{_as_int(row.get('bar'))} gün",
            str(row.get("durum") or row.get("stage") or "durum bilinmiyor"),
            f"{_as_int(row.get('temas'))} temas",
        ]
        if row.get("son_tarih"):
            detail_parts.append(f"veri: {str(row.get('son_tarih'))[:10]}")
        if is_bist100:
            detail_parts.insert(0, "BIST100")
        items.append(
            {
                "symbol": symbol,
                "target": row.get("sembol") or symbol,
                "icon": "📐",
                "label": row.get("ad") or "Çizgi Yapısı",
                "price": price,
                "rank": (
                    f"BIST100 + {len(other_sources)} tarama"
                    if is_bist100 and other_sources
                    else ("BIST100" if is_bist100 else f"+{len(other_sources)} tarama")
                ),
                "detail": " · ".join(detail_parts),
                "status": (
                    f"Birlikte çıkan taramalar: {source_text}"
                    if other_sources
                    else "Yalnızca Çizgi Yapısı bulundu."
                ),
                "_bist100": is_bist100,
                "_other_count": len(other_sources),
                "_ciro": _as_float(row.get("ciro")),
                "_sources": source_text,
            }
        )

    # Önce BIST100 + kesişim, sonra diğer kesişimler, sonra yalnız Çizgi Yapısı.
    # Her grubun iç sırası likiditeye bırakılır; ölçüm yoksa ortak fotoğraftaki
    # 20 günlük medyan ciro yalnızca güvenli geri dönüş sırasıdır.
    groups = ([], [], [], [])
    for item in items:
        if item["_bist100"] and item["_other_count"]:
            groups[0].append(item)
        elif item["_other_count"]:
            groups[1].append(item)
        elif item["_bist100"]:
            groups[2].append(item)
        else:
            groups[3].append(item)

    ordered_items: list[dict[str, Any]] = []
    measured_any = False
    for group in groups:
        group.sort(key=lambda item: item.get("_ciro", 0.0), reverse=True)
        if not group:
            continue
        symbols = [item["symbol"] for item in group]
        liquidity_order, measured = _liquidity_top(
            symbols,
            as_of=as_of,
            scan_type="cizgi_yapi",
            limit=len(symbols),
        )
        measured_any = measured_any or measured
        by_symbol = {item["symbol"]: item for item in group}
        if measured:
            ordered_items.extend(
                by_symbol[symbol] for symbol in liquidity_order if symbol in by_symbol
            )
            ordered_symbols = set(liquidity_order)
            ordered_items.extend(
                item for item in group if item["symbol"] not in ordered_symbols
            )
        else:
            ordered_items.extend(group)
    return ordered_items, measured_any


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
            st.markdown("**T+3 teyitli**")
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


def _build_magic_ribbon_items(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """4S Magic Ribbon sonuçlarını Tarama Merkezi'nin ortak satır formatına çevirir."""
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    items: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        symbol = str(row.get("Sembol") or "").replace(".IS", "").upper()
        if not symbol:
            continue
        status = str(row.get("Durum") or "HİZALANMA SÜRÜYOR")
        try:
            price_text = f"{float(row.get('Fiyat')):.2f}"
        except (TypeError, ValueError):
            price_text = "—"
        try:
            age_bars = int(row.get("TetikYaşı"))
        except (TypeError, ValueError):
            age_bars = 0
        try:
            up_bars = int(row.get("YukarıBar"))
        except (TypeError, ValueError):
            up_bars = 0
        age_text = "son kapanmış bar" if age_bars == 0 else f"{age_bars} kapanmış 4S bar önce"
        items.append({
            "symbol": symbol,
            "target": f"{symbol}.IS",
            "price": price_text,
            "icon": "⏱" if age_bars == 0 else "↗",
            "label": "Yeni 4S yukarı hizalanma" if age_bars == 0 else "4S yukarı hizalanma sürüyor",
            "detail": (
                f"Fast ve Slow çizgileri yukarı eğimli · {up_bars} kapanmış 4S bar · "
                f"son hizalanma {age_text}"
            ),
            "status": f"BIST100 filtresi · son kapanış {row.get('SonBar', '—')}",
        })
    return items


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
    _catalog_count = len({
        symbol
        for category in catalog
        for symbol in category.get("symbols", [])
    })
    # 29 Ağu 2026 — 5. sütun: OLASI SHORT (er_D4 / er_D5); 6. sütun: ÇİZGİ YAPISI.
    # Ayni kaynak app.py'deki OLASI SHORT paneliyle: erken_radar_data'daki
    # D4/D5 satirlari, tekil hisse sayisi, endeksler haric.
    # ⚠ Bu bir ALIM/SATIM tavsiyesi degil, uyari/veto katmanidir: islem
    # yapilabilir evrende (VIOP 47 / BIST50) kenar olcumde cokuyor.
    _short_count = 0
    try:
        _er_sh = session_getter('erken_radar_data')
        if _er_sh is not None and hasattr(_er_sh, 'empty') and not _er_sh.empty \
                and 'ScenarioId' in _er_sh.columns and 'Sembol' in _er_sh.columns:
            import likidite_siralama as _ls_sc
            _sh_rows = _er_sh[_er_sh['ScenarioId'].astype(str).isin(['D4', 'D5'])]
            _short_count = len({
                str(_x).replace('.IS', '').upper()
                for _x in _sh_rows['Sembol'].dropna()
                if _ls_sc.hisse_mi(_x)
            })
    except Exception:
        _short_count = 0
    _cizgi_items, _cizgi_liquidity_measured = _cizgi_master_items(
        session_getter,
        catalog,
        as_of=as_of,
    )
    _magic_ribbon_df = session_getter("magic_ribbon_4s_data")
    _magic_ribbon_rows = _build_magic_ribbon_items(_magic_ribbon_df)
    _magic_ribbon_count = len(_magic_ribbon_rows)
    _summary_items = (
        ("🎯 T+3 Teyitli", counts[BUCKET_READY], "#22c55e"),
        ("⏳ T+1 & T+2 Onaylılar", counts[BUCKET_GROWING] + counts[BUCKET_WATCH], "#f59e0b"),
        ("🌱 Yeni Sinyal T+0", counts[BUCKET_NEW], "#38bdf8"),
        ("⚠️ Risk masası", counts[BUCKET_RISK], "#ef4444"),
        ("📉 Olası short", _short_count, "#fca5a5"),
        ("⏱ 4S Yukarı Hizalanma", _magic_ribbon_count, "#f59e0b"),
        ("📐 Çizgi Yapısı", len(_cizgi_items), "#38bdf8"),
    )
    _summary_html = "".join(
        f"<div style='background:#0f172acc;border:1px solid #263b5d;border-radius:8px;"
        f"padding:9px 11px;'><div style='font-size:0.66rem;color:#94a3b8;'>"
        f"{label}</div><div style='font-size:1.18rem;font-weight:900;color:{color};"
        f"margin-top:2px;'>{count}</div></div>"
        for label, count, color in _summary_items
    )
    st.markdown(
        "<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin:6px 0 12px 0;'>"
        "<div><div style='font-size:1.10rem;font-weight:900;color:#38bdf8;'>🎯 Tarama Merkezi</div>"
        "<div style='font-size:0.72rem;color:#94a3b8;margin-top:3px;'>Bugün karar verilecek adayları tek bakışta ayır.</div></div>"
        f"<div style='font-size:0.72rem;color:#e2e8f0;text-align:right;white-space:nowrap;'>📅 {html.escape(as_of)} kapanışı"
        "<div style='font-size:0.64rem;color:#64748b;margin-top:2px;'>Master Scan fotoğrafı</div></div>"
        "</div>"
        f"<div style='display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;margin:0 0 12px 0;'>"
        f"{_summary_html}</div>",
        unsafe_allow_html=True,
    )
    if desk["catalog_only"]:
        st.caption("Kapanış takip fotoğrafı henüz oluşmadı; bu görünüm yalnızca T0 aday havuzudur.")

    _scan_karne_sorun = _scan_karne_issue()
    if _scan_karne_sorun:
        st.warning(
            f"⚠ Tarama karnesi doğrulama uyarısı: {_scan_karne_sorun} "
            "Kartlardaki geçmiş ölçüm güncel kabul edilmemeli."
        )

    @st.dialog("🔍 Kurulum Detayı", width="large")
    def open_detail(candidate: dict[str, Any]) -> None:
        meta = BUCKET_META[candidate["bucket"]]
        _masa = str(candidate.get("vade_masasi") or "KATALOG")
        _vade = str(candidate.get("vade") or "T+20")
        _expiry = str(candidate.get("son_kullanma_tarihi") or "hesaplanamadı")[:10]
        _karne = str(candidate.get("gecmis_karne") or "BİLMİYORUZ · ölçüm kaydı yok")
        _story = _card_story(candidate)
        st.markdown(
            f"<div style='font-size:0.68rem;font-weight:800;color:{meta['color']};'>{meta['tag']} · DETAY</div>"
            f"<div style='font-size:1.25rem;font-weight:900;'>{html.escape(candidate['sym'])} — {html.escape(_story)}</div>",
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
            f"- Kaynaklar: {', '.join(_display_scan(scan) for scan in _card_scan_types(candidate)) or '—'}"
        )
        if candidate.get("flow_alignment"):
            st.info(
                "💧 CMF + OBV olumlu — CMF20 pozitif ve OBV yönü pozitif. "
                "Bu yalnızca bilgi rozetidir; puan, vade veya eylem hükmü değildir."
            )
        if candidate["crowded"]:
            st.warning(f"Kalabalık uyarısı: {candidate['scan_count']} taramada görünmüş. Bu bilgi puan değildir.")
        if candidate["bucket"] in (BUCKET_WATCH, BUCKET_RISK):
            st.warning(candidate["note"])
        if st.button("📊 Tam hisse analizini aç", width="stretch", key=f"trajectory_full_{candidate['sym']}_{candidate['event_start_date']}"):
            on_click(candidate["ticker"])
            st.session_state["_tm_scroll_top"] = True
            st.rerun(scope="app")

    # 🗂️ KARAR YÜZEYİ — ürünce onaylı sekmeler.
    # Yaşam döngüsünün iki ara durumu aynı sekmede kalır; içeride ayrı masalara
    # bölünerek geniş ara havuzun işlem listesi gibi görünmesi engellenir.
    tab_long, tab_confirm, tab_new, tab_risk, tab_catalog, tab_short, tab_magic, tab_cizgi = st.tabs([
        f"🎯 T+3 Teyitli ({counts[BUCKET_READY]} aday)",
        f"⏳ T+1 & T+2 Onaylılar ({counts[BUCKET_GROWING] + counts[BUCKET_WATCH]} aday)",
        f"🌱 Yeni Sinyal T+0 ({counts[BUCKET_NEW]})",
        f"⚠️ Risk Masası ({counts[BUCKET_RISK]})",
        f"📚 Katalog ({_catalog_count})",
        f"📉 Olası Short ({_short_count})",
        f"⏱ 4S Yukarı ({_magic_ribbon_count})",
        f"📐 Çizgi Yapısı ({len(_cizgi_items)})",
    ])

    def _render_decision_groups(
        tab: Any,
        groups: list[str],
        leading: Any = None,
    ) -> None:
        with tab:
            values = [candidate for bucket in groups for candidate in desk[bucket]]
            view_meta = {
                (BUCKET_READY,): (
                    "🎯 T+3 Teyitli Adaylar",
                    "T+3 kontrolü tamamlanan; ilk bakışta likiditesi yüksek 5 aday öne çıkar",
                ),
                (BUCKET_GROWING, BUCKET_WATCH): (
                    "⏳ T+1 & T+2 Onaylılar",
                    "T+1/T+2'de güçlenenler üstte; teyit eksiği olanlar içeride ayrı izleme grubunda tutulur",
                ),
                (BUCKET_NEW,): (
                    "🌱 Yeni Sinyal T+0",
                    "İlk kez yakalananlar; geçmiş teyit süreci henüz oluşmadı",
                ),
                (BUCKET_RISK,): (
                    "⚠️ Risk Masası",
                    "Veto, zayıflama ve düşüş uyarıları; işlem listesi değildir",
                ),
            }
            title, note = view_meta.get(tuple(groups), ("Tarama listesi", ""))
            color = BUCKET_META[groups[0]]["color"]
            st.markdown(
                f"<div style='font-size:0.98rem;font-weight:900;color:{color};margin:2px 0 1px 0;'>"
                f"{title} · {len(values)} aday</div>"
                f"<div style='font-size:0.68rem;color:#64748b;margin:0 0 12px 2px;'>{note}</div>",
                unsafe_allow_html=True,
            )
            if leading is not None:
                leading()
            if values:
                if tuple(groups) == (BUCKET_READY,):
                    _render_ready_priority(
                        st,
                        values,
                        open_detail,
                        as_of=as_of,
                    )
                elif tuple(groups) == (BUCKET_GROWING, BUCKET_WATCH):
                    _render_confirm_pool(
                        st,
                        desk,
                        open_detail,
                        as_of=as_of,
                    )
                else:
                    _render_grid(
                        st,
                        values,
                        open_detail,
                        key_prefix="_".join(groups),
                    )
            else:
                empty_text = (
                    "Takip veya teyit bekleyen kurulum yok."
                    if len(groups) > 1
                    else {
                        BUCKET_READY: "Henüz T+3 karar kontrolünü geçen aday yok.",
                        BUCKET_GROWING: "Takipte güçlenen aday yok.",
                        BUCKET_NEW: "Yeni aday yok.",
                        BUCKET_WATCH: "İzleme gerektiren aday yok.",
                        BUCKET_RISK: "Sert risk vetosu alan hisse yok.",
                    }[groups[0]]
                )
                st.markdown(
                    "<div style='border:1px dashed #334155;border-radius:6px;padding:12px;text-align:center;"
                    f"color:#94a3b8;font-size:0.75rem;margin:10px 0;'>{empty_text}</div>",
                    unsafe_allow_html=True,
                )

    _render_decision_groups(tab_long, [BUCKET_READY])
    _render_decision_groups(tab_confirm, [BUCKET_GROWING, BUCKET_WATCH])
    _render_decision_groups(tab_new, [BUCKET_NEW])
    _render_decision_groups(
        tab_risk,
        [BUCKET_RISK],
    )

    with tab_short:
        _render_short_warning(
            st, session_getter, on_click, as_of=as_of, show_empty=True
        )

    with tab_magic:
        st.markdown(
            f"<div style='font-size:0.98rem;font-weight:900;color:#f59e0b;margin:2px 0 1px 0;'>"
            f"⏱ 4S Yukarı Hizalanma · {_magic_ribbon_count} sonuç</div>"
            "<div style='font-size:0.68rem;color:#64748b;margin:0 0 12px 2px;'>"
            "Yalnız BIST100 içindeki hisselerde, kapanmış ve taze 4S barlarda Fast/Slow "
            "çizgileri aynı anda yukarı eğimli olan gözlem adayları.</div>",
            unsafe_allow_html=True,
        )
        if _magic_ribbon_df is None:
            st.markdown(
                "<div style='border:1px dashed #f59e0b55;border-radius:7px;padding:12px;"
                "text-align:center;color:#94a3b8;font-size:0.75rem;'>Master Scan çalıştırın</div>",
                unsafe_allow_html=True,
            )
        else:
            render_standard_scan_list(
                st,
                _magic_ribbon_rows,
                lambda item: (on_click(item.get("target") or item.get("symbol")), st.rerun(scope="app")),
                key_prefix="magic_ribbon_4s",
                priority_title="⏱ İLK BAKILACAK 5",
                priority_note=(
                    "Yeni hizalanmalar önce; bu ekran yön gözlemidir, bağımsız teyit veya işlem emri değildir."
                ),
                priority_color="#f59e0b",
                empty_text="BIST100 içinde güncel, kapanmış 4S yukarı hizalanması bulunamadı.",
            )

    with tab_cizgi:
        st.markdown(
            f"<div style='font-size:0.98rem;font-weight:900;color:#38bdf8;margin:2px 0 1px 0;'>"
            f"📐 Çizgi Yapısı · {len(_cizgi_items)} sonuç</div>"
            "<div style='font-size:0.68rem;color:#64748b;margin:0 0 12px 2px;'>"
            "BIST100 + başka tarama kesişimleri önce; sonra diğer kesişimler; "
            "her grubun içinde 20 günlük TL likiditesi.</div>",
            unsafe_allow_html=True,
        )
        if not _cizgi_liquidity_measured and _cizgi_items:
            st.caption(
                "Likidite ölçümü hazır değil; ortak Master Scan fotoğrafındaki 20 günlük ciro sırası kullanıldı."
            )
        render_standard_scan_list(
            st,
            _cizgi_items,
            lambda item: (on_click(item.get("target") or item.get("symbol")), st.rerun(scope="app")),
            key_prefix="cizgi_yapi_master",
            priority_title="📐 İLK BAKILACAK 5",
            priority_note=(
                "BIST100 içinde başka taramalarda da çıkanlar üstte; "
                "aynı grubun içi likiditeye göre sıralıdır."
            ),
            priority_color="#38bdf8",
            empty_text=(
                "Bu Master Scan fotoğrafında elekten geçen Çizgi Yapısı sonucu yok."
            ),
        )

    with tab_catalog:
        _render_goldmine_liquidity(st, desk, on_click)
        st.markdown(
            "<div style='font-size:0.92rem;font-weight:900;color:#e2e8f0;margin:16px 0 2px 0;'>"
            "📚 Tarama Kataloğu</div>"
            "<div style='font-size:0.66rem;color:#64748b;margin:0 0 6px 2px;'>"
            "Ham sonuçlar denetim alanıdır; karar listesine puan taşımaz.</div>",
            unsafe_allow_html=True,
        )
        for category in catalog:
            if not category["count"]:
                continue
            with st.expander(
                f"{category['name']} · {category['count']} sonuç · {category['family']}"
            ):
                for index, symbol in enumerate(category["symbols"]):
                    if st.button(
                        symbol,
                        key=f"trajectory_catalog_{category['key']}_{symbol}_{index}",
                        width="stretch",
                    ):
                        on_click(symbol + ".IS" if "." not in symbol else symbol)
                        st.rerun(scope="app")

    _render_live_karne(st)
