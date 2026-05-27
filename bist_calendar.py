"""
bist_calendar.py — Borsa İstanbul İşlem Takvimi
================================================
Milli tatiller, dini bayramlar ve arefe yarım günleri.

Kullanım:
    from bist_calendar import (
        is_trading_day, is_half_day, get_rvol_day_factor,
        get_session_hours, get_day_label, get_day_status
    )

Arefe hacim normalizer:
    Arefe günü BIST 10:00–12:30 arası açık (150 dk).
    Normal gün 10:00–18:00 (480 dk).
    Oran = 150/480 ≈ 0.3125
    → RVOL normalize = raw_rvol / 0.3125 (arefe günü beklentisi %31)

Güncelleme:
    Dini bayram tarihleri Diyanet İşleri Başkanlığı resmi açıklamalarıyla
    yılda bir kez güncellenir. Onaylı kaynaklar:
      https://www.diyanet.gov.tr/
      https://www.borsaistanbul.com/duyurular
"""

from __future__ import annotations
from datetime import date, datetime
import pytz

# ── Sabitler ───────────────────────────────────────────────────────────────────
_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")

# Arefe seans: 10:00–12:30 = 150 dk  |  Normal seans: 10:00–18:00 = 480 dk
AREFE_SESSION_MINUTES  = 150
NORMAL_SESSION_MINUTES = 480
AREFE_SESSION_RATIO    = AREFE_SESSION_MINUTES / NORMAL_SESSION_MINUTES  # ≈ 0.3125

NORMAL_OPEN  = "10:00"
NORMAL_CLOSE = "18:00"
AREFE_OPEN   = "10:00"
AREFE_CLOSE  = "12:30"

# ── Sabit Milli Tatiller (ay, gün) ─────────────────────────────────────────────
# Kaynak: Resmî Gazete / Türk iş takvimleri
FIXED_NATIONAL_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1):   "Yılbaşı",
    (4, 23):  "Ulusal Egemenlik ve Çocuk Bayramı",
    (5, 1):   "İşçi ve Emekçi Bayramı",
    (5, 19):  "Atatürk'ü Anma, Gençlik ve Spor Bayramı",
    (7, 15):  "Demokrasi ve Millî Birlik Günü",
    (8, 30):  "Zafer Bayramı",
    (10, 29): "Cumhuriyet Bayramı",
}

# ── Dini Bayram + Arefe Takvimi ─────────────────────────────────────────────────
# "closed" = tam kapalı  |  "half" = arefe günü (10:00–12:30)
# Kaynak: Diyanet İşleri Başkanlığı resmî açıklamaları
# ⚠️  ±1 gün sapma mümkün (hilal görünümüne bağlı) — Diyanet onayı zorunlu.
#
# Güncelleme sırası:
#   1. Diyanet açıklamasını kontrol et.
#   2. İlgili yılı RELIGIOUS_CALENDAR'a ekle.
#   3. Commit + VPS deploy.
RELIGIOUS_CALENDAR: dict[date, tuple[str, str]] = {

    # ╔══════════════════════════════╗
    # ║          2026                ║
    # ╚══════════════════════════════╝
    # Ramazan Bayramı: 20–22 Mart 2026  |  Arefe: 19 Mart
    date(2026, 3, 19): ("half",   "Ramazan Bayramı Arefe"),
    date(2026, 3, 20): ("closed", "Ramazan Bayramı 1. Gün"),
    date(2026, 3, 21): ("closed", "Ramazan Bayramı 2. Gün"),
    date(2026, 3, 22): ("closed", "Ramazan Bayramı 3. Gün"),
    # Kurban Bayramı: 27–30 Mayıs 2026  |  Arefe: 26 Mayıs
    date(2026, 5, 26): ("half",   "Kurban Bayramı Arefe"),
    date(2026, 5, 27): ("closed", "Kurban Bayramı 1. Gün"),
    date(2026, 5, 28): ("closed", "Kurban Bayramı 2. Gün"),
    date(2026, 5, 29): ("closed", "Kurban Bayramı 3. Gün"),
    date(2026, 5, 30): ("closed", "Kurban Bayramı 4. Gün"),

    # ╔══════════════════════════════╗
    # ║          2027                ║
    # ╚══════════════════════════════╝
    # Ramazan Bayramı: 9–11 Mart 2027  |  Arefe: 8 Mart
    date(2027, 3,  8): ("half",   "Ramazan Bayramı Arefe"),
    date(2027, 3,  9): ("closed", "Ramazan Bayramı 1. Gün"),
    date(2027, 3, 10): ("closed", "Ramazan Bayramı 2. Gün"),
    date(2027, 3, 11): ("closed", "Ramazan Bayramı 3. Gün"),
    # Kurban Bayramı: 16–19 Mayıs 2027  |  Arefe: 15 Mayıs
    date(2027, 5, 15): ("half",   "Kurban Bayramı Arefe"),
    date(2027, 5, 16): ("closed", "Kurban Bayramı 1. Gün"),
    date(2027, 5, 17): ("closed", "Kurban Bayramı 2. Gün"),
    date(2027, 5, 18): ("closed", "Kurban Bayramı 3. Gün"),
    date(2027, 5, 19): ("closed", "Kurban Bayramı 4. Gün"),

    # ╔══════════════════════════════╗
    # ║          2028                ║
    # ╚══════════════════════════════╝
    # Ramazan Bayramı: 27 Şub–1 Mar 2028  |  Arefe: 26 Şubat
    date(2028, 2, 26): ("half",   "Ramazan Bayramı Arefe"),
    date(2028, 2, 27): ("closed", "Ramazan Bayramı 1. Gün"),
    date(2028, 2, 28): ("closed", "Ramazan Bayramı 2. Gün"),
    date(2028, 2, 29): ("closed", "Ramazan Bayramı 3. Gün"),  # 2028 artık yıl
    # Kurban Bayramı: 5–8 Mayıs 2028  |  Arefe: 4 Mayıs
    date(2028, 5,  4): ("half",   "Kurban Bayramı Arefe"),
    date(2028, 5,  5): ("closed", "Kurban Bayramı 1. Gün"),
    date(2028, 5,  6): ("closed", "Kurban Bayramı 2. Gün"),
    date(2028, 5,  7): ("closed", "Kurban Bayramı 3. Gün"),
    date(2028, 5,  8): ("closed", "Kurban Bayramı 4. Gün"),
}


# ── Yardımcı: dt normalizer ─────────────────────────────────────────────────────
def _to_date(dt) -> date:
    """datetime / date / None → Istanbul tarihine normalize et."""
    if dt is None:
        return datetime.now(_TZ_ISTANBUL).date()
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = _TZ_ISTANBUL.localize(dt)
        return dt.astimezone(_TZ_ISTANBUL).date()
    return dt  # zaten date nesnesi


# ── Ana Fonksiyon ───────────────────────────────────────────────────────────────
def get_day_status(dt=None) -> tuple[str, str]:
    """
    Verilen günün BIST işlem durumunu döner.

    Returns:
        ("open",   "Normal Seans")           — işlem günü
        ("half",   "Ramazan Bayramı Arefe")  — arefe (10:00–12:30)
        ("closed", "Kurban Bayramı 1. Gün")  — tam tatil
        ("closed", "Cumartesi")              — hafta sonu
    """
    d = _to_date(dt)

    # 1. Hafta sonu
    if d.weekday() == 5:
        return ("closed", "Cumartesi")
    if d.weekday() == 6:
        return ("closed", "Pazar")

    # 2. Dini bayram / arefe
    if d in RELIGIOUS_CALENDAR:
        return RELIGIOUS_CALENDAR[d]

    # 3. Sabit milli tatil
    key = (d.month, d.day)
    if key in FIXED_NATIONAL_HOLIDAYS:
        return ("closed", FIXED_NATIONAL_HOLIDAYS[key])

    # 4. Normal işlem günü
    return ("open", "Normal Seans")


# ── Türev Yardımcılar ───────────────────────────────────────────────────────────
def is_trading_day(dt=None) -> bool:
    """True → bugün BIST'te işlem var (normal veya arefe)."""
    status, _ = get_day_status(dt)
    return status != "closed"


def is_half_day(dt=None) -> bool:
    """True → bugün arefe günü (10:00–12:30 seans)."""
    status, _ = get_day_status(dt)
    return status == "half"


def is_closed(dt=None) -> bool:
    """True → tam tatil (dahil hafta sonu)."""
    status, _ = get_day_status(dt)
    return status == "closed"


def get_session_hours(dt=None) -> tuple[str, str] | None:
    """
    BIST seans saatlerini döner.
      None           → kapalı
      ("10:00","18:00") → normal gün
      ("10:00","12:30") → arefe
    """
    status, _ = get_day_status(dt)
    if status == "closed":
        return None
    if status == "half":
        return (AREFE_OPEN, AREFE_CLOSE)
    return (NORMAL_OPEN, NORMAL_CLOSE)


def get_rvol_day_factor(dt=None) -> float:
    """
    Hacim normalizer katsayısı.

    Normal gün  → 1.0
    Arefe günü  → 0.3125  (150 / 480)

    Kullanım:
        rvol_normalized = raw_vol / (avg_vol_20d * get_rvol_day_factor())

    Böylece arefe günü beklenen hacmin %100'ü = rvol_normalized 1.0
    (raw_vol bağlamında bu avg_vol'un %31'ine karşılık gelir)
    """
    return AREFE_SESSION_RATIO if is_half_day(dt) else 1.0


def get_day_label(dt=None) -> str:
    """
    UI için kısa durum etiketi.
    Örnekler:
      "✅ Normal Seans"
      "⚠️ Arefe — Kurban Bayramı (12:30)"
      "⛔ Kurban Bayramı 1. Gün"
      "⛔ Cumartesi"
    """
    status, name = get_day_status(dt)
    if status == "open":
        return "✅ Normal Seans"
    if status == "half":
        _bayram = name.replace(" Arefe", "")
        return f"⚠️ Arefe — {_bayram} ({AREFE_CLOSE})"
    return f"⛔ {name}"


def get_arefe_progress(dt=None) -> float | None:
    """
    Arefe günü içindeki ilerleme oranını döner (0.0–1.0).
    Arefe değilse None.
    Kullanım: apply_volume_projection içinde arefe seansı için progress hesabı.
    """
    if not is_half_day(dt):
        return None
    now = datetime.now(_TZ_ISTANBUL)
    open_min  = 10 * 60        # 10:00
    close_min = 12 * 60 + 30   # 12:30
    now_min   = now.hour * 60 + now.minute
    if now_min < open_min:
        return 0.0
    if now_min >= close_min:
        return 1.0
    return (now_min - open_min) / (close_min - open_min)
