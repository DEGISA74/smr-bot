"""
produce_json.py — SMR Public Vitrin Cron Job
---------------------------------------------
VM'de her gece 19:30'da çalışır:
    30 19 * * 1-5 python3 /path/to/produce_json.py

Görev:
  1. scan_core.py → latest.json üret
  2. Google Cloud Storage'a yükle (bucket: smr-public-data)
  3. Hata olursa eski latest.json korunur, Telegram'a uyarı gider
"""

import os
import sys
import json
import shutil
import datetime
import traceback

# scan_core aynı klasörde
sys.path.insert(0, os.path.dirname(__file__))
from scan_core import build_latest_json

# ── Ayarlar ──────────────────────────────────────────────────────────────────
BUCKET_NAME    = "smr-public-data"           # GCS bucket adı

# Windows/Linux uyumlu path
_HERE          = os.path.dirname(os.path.abspath(__file__))
_FRONTEND      = os.path.join(_HERE, "..", "frontend")
OUTPUT_FILE    = os.path.join(_FRONTEND, "latest.json")   # Doğrudan frontend'e yaz
BACKUP_FILE    = os.path.join(_HERE, "latest_backup.json")
LOCAL_COPY     = os.path.join(_HERE, "latest.json")        # Backend'de de kopya kalsın

# Telegram uyarısı (opsiyonel — .env'den okur)
TG_TOKEN       = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_ADMIN_CHAT  = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
# ─────────────────────────────────────────────────────────────────────────────


def upload_to_gcs(local_path: str, bucket: str, blob_name: str = "latest.json") -> bool:
    """Google Cloud Storage'a yükler. google-cloud-storage paketi gerekir."""
    try:
        from google.cloud import storage
        client = storage.Client()
        b      = client.bucket(bucket)
        blob   = b.blob(blob_name)
        blob.upload_from_filename(local_path, content_type="application/json")
        blob.make_public()
        print(f"[GCS] ✓ gs://{bucket}/{blob_name} yüklendi")
        return True
    except ImportError:
        print("[GCS] google-cloud-storage kurulu değil, sadece yerel dosya yazıldı.")
        return False
    except Exception as e:
        print(f"[GCS] ✗ Yükleme hatası: {e}")
        return False


def send_telegram_alert(msg: str):
    """Admin'e Telegram mesajı gönderir."""
    if not TG_TOKEN or not TG_ADMIN_CHAT:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_ADMIN_CHAT, "text": msg},
            timeout=10
        )
    except Exception:
        pass


def validate_json(payload: dict) -> bool:
    """Temel veri kalite kontrolü."""
    try:
        xu100 = payload.get("xu100", {})
        ozet  = payload.get("piyasa_ozeti", {})

        # XU100 verisi geldi mi?
        if "hata" in xu100:
            return False
        if not xu100.get("kapanis"):
            return False

        # En az 50 hisse tarandı mı?
        if ozet.get("taranan", 0) < 50:
            return False

        return True
    except Exception:
        return False


def run():
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n{'='*50}")
    print(f"SMR Public JSON Üretimi — {now}")
    print(f"{'='*50}\n")

    # Eski yedek al
    if os.path.exists(OUTPUT_FILE):
        shutil.copy(OUTPUT_FILE, BACKUP_FILE)
        print(f"[yedek] Eski dosya yedeklendi → {BACKUP_FILE}")

    try:
        # 1. JSON üret
        payload = build_latest_json(OUTPUT_FILE)

        # 2. Kalite kontrolü
        if not validate_json(payload):
            raise ValueError("Kalite kontrolü başarısız — yetersiz veri")

        # 3. Yerel kopya
        shutil.copy(OUTPUT_FILE, LOCAL_COPY)
        print(f"[yerel] Kopya → {LOCAL_COPY}")

        # 4. GCS'e yükle
        upload_to_gcs(OUTPUT_FILE, BUCKET_NAME)

        # 5. Özet
        xu100 = payload["xu100"]
        ozet  = payload["piyasa_ozeti"]
        print(f"\n✅ Başarılı")
        print(f"   XU100   : {xu100.get('kapanis')} ({xu100.get('degisim_pct'):+.2f}%)")
        print(f"   Rejim   : {xu100.get('rejim')}")
        print(f"   Taranan : {ozet.get('taranan')} hisse")
        print(f"   SMA200+ : %{ozet.get('sma200_ustu_pct')}")
        print(f"   Skor    : {ozet.get('genel_skor')}/100\n")

    except Exception as e:
        hata_mesaji = traceback.format_exc()
        print(f"\n❌ HATA:\n{hata_mesaji}")

        # Eski dosyayı geri yükle
        if os.path.exists(BACKUP_FILE):
            shutil.copy(BACKUP_FILE, OUTPUT_FILE)
            shutil.copy(BACKUP_FILE, LOCAL_COPY)
            print("[yedek] Eski latest.json geri yüklendi.")

        # Admin'e uyarı
        send_telegram_alert(
            f"⚠️ SMR Public JSON üretimi başarısız!\n"
            f"Tarih: {now}\n"
            f"Hata: {str(e)[:200]}"
        )
        sys.exit(1)


if __name__ == "__main__":
    run()
