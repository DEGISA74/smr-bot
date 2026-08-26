#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Patron Terminal VPS deploy (Claude + Codex ORTAK ARAÇ)
#
# Ne yapar: değişeni kendi bulur → ezme kontrolü → yerel syntax → VPS yedek →
#           scp → VPS py_compile → "import app" → restart → health →
#           bozulursa OTOMATİK GERİ ALIR.
#
# Kullanım:
#   ./deploy.sh                        # KURU ÇALIŞMA (göndermez, ne gideceğini yazar)
#   ./deploy.sh --go                   # gerçek deploy
#   ./deploy.sh --go --only=app.py,evidence.py
#   ./deploy.sh --go --bot             # smr-bot'u da restart et (smr_core değiştiyse)
#   ./deploy.sh --go --force           # ezme uyarısını geç (ÖNCE DÜŞÜN)
#
# VPS'te `git pull` YOK — deploy SCP-ONLY. Protokol: IKI_AJAN_VPS_DEPLOY.md
# =============================================================================
set -uo pipefail

VPS="wm11tr@34.153.19.220"
UZAK="~/smr"
SERVISLER="patron-radar free-showcase"
SAGLIK="http://127.0.0.1/patron/"

DOSYALAR="app.py data_layer.py indicators.py db_layer.py evidence.py scanners.py
pattern_core.py ict_core.py scoring_core.py scan_pipeline.py charts.py
analysis_core.py terazi_core.py veri_bekcisi.py saatlik_kapi.py saatlik_uzlasma.py zamanlama_core.py depo_tazelik.py
seans_profili.py
formasyon_core.py formasyon_v2.py cizgi_yapi.py cizgi_alarm.py
formasyon_v2_app.py sampiyonlar_ligi.py smr_core.py infografik_build.py fetcher.py
data_policy.py bist_calendar.py master_scan_progress.py master_scan_giris_senaryolari.py
trajectory_tarama_merkezi.py tarama_merkezi.py pusula_engine.py"

GO=0; BOT=0; FORCE=0; ONLY=""
for a in "$@"; do
  case "$a" in
    --go) GO=1 ;;
    --bot) BOT=1 ;;
    --force) FORCE=1 ;;
    --only=*) ONLY="${a#--only=}" ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
  esac
done

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
ok(){    printf '  \033[32mOK\033[0m   %s\n' "$*"; }
uyar(){  printf '  \033[33mUYARI\033[0m %s\n' "$*"; }
hata(){  printf '  \033[31mHATA\033[0m %s\n' "$*"; }
baslik(){ printf '\n\033[1m%s\033[0m\n' "$*"; }

# satır sonu farkını yok sayan hash (lokal CRLF ↔ VPS LF sahte fark üretmesin)
HASHCMD='awk "{sub(/\r$/,\"\")};1" "$0" | md5sum | cut -d" " -f1'

if [ -n "$ONLY" ]; then LISTE=$(echo "$ONLY" | tr ',' ' '); else LISTE=$(echo "$DOSYALAR" | tr '\n' ' '); fi
VAR=""; for f in $LISTE; do [ -f "$f" ] && VAR="$VAR $f"; done
VAR=$(echo $VAR | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')
[ -z "$(echo $VAR)" ] && { hata "dosya bulunamadı"; exit 1; }

baslik "1) DEĞİŞEN DOSYALAR"
for f in $VAR; do
  echo "$f $(awk '{sub(/\r$/,"")};1' "$f" | md5sum | cut -d' ' -f1)"
done | sort > "$TMP/local.txt"

ssh -o ConnectTimeout=15 "$VPS" "cd $UZAK && for f in $VAR; do [ -f \$f ] && echo \"\$f \$(awk '{sub(/\r\$/,\"\")};1' \$f | md5sum | cut -d' ' -f1)\"; done" 2>/dev/null | sort > "$TMP/vps.txt"
[ -s "$TMP/vps.txt" ] || { hata "VPS'e bağlanılamadı / dosya okunamadı"; exit 1; }

DEGISEN=$(join "$TMP/local.txt" "$TMP/vps.txt" | awk '$2!=$3{print $1}')
YENI=$(comm -23 <(cut -d' ' -f1 "$TMP/local.txt") <(cut -d' ' -f1 "$TMP/vps.txt"))
GIDECEK=$(echo "$DEGISEN $YENI" | tr ' ' '\n' | grep -v '^$' | sort -u | tr '\n' ' ')

if [ -z "$(echo $GIDECEK)" ]; then ok "Her şey güncel — gönderilecek dosya yok."; exit 0; fi
for f in $DEGISEN; do echo "  değişti : $f"; done
for f in $YENI;    do echo "  YENİ    : $f (VPS'te yok)"; done

baslik "2) EZME (CLOBBER) KONTROLÜ — VPS'te olup lokalde olmayan içerik"
RISK=0
for f in $DEGISEN; do
  scp -q "$VPS:$UZAK/$f" "$TMP/v_$(basename $f)" 2>/dev/null || continue
  N=$(diff "$TMP/v_$(basename $f)" "$f" 2>/dev/null | grep -cE '^[0-9,]+d[0-9]+$')
  if [ "${N:-0}" -gt 0 ]; then uyar "$f — VPS'e özel $N blok (diğer ajanın işi olabilir)"; RISK=1
  else ok "$f"; fi
done
if [ "$RISK" = "1" ] && [ "$FORCE" != "1" ]; then
  hata "DUR — önce incele:  scp $VPS:$UZAK/DOSYA /tmp/v && diff /tmp/v DOSYA"
  hata "Bilerek ezeceksen: --force"
  exit 2
fi

baslik "3) YEREL SYNTAX"
for f in $GIDECEK; do
  case "$f" in *.py)
    python -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8').read())" "$f" 2>/dev/null \
      && ok "$f" || { hata "$f SYNTAX HATASI — iptal"; exit 3; } ;;
  esac
done

if [ -f golden_record.json ]; then
  for f in $GIDECEK; do
    [ "$f" -nt golden_record.json ] && { uyar "$f golden'dan yeni — hesap değiştiyse: python golden_record.py"; break; }
  done
fi

if [ "$GO" != "1" ]; then
  baslik "KURU ÇALIŞMA — hiçbir şey gönderilmedi."
  echo "  Göndermek için: ./deploy.sh --go"
  exit 0
fi

DMG=$(date +%Y%m%d_%H%M%S)
baslik "4) VPS YEDEK + GÖNDER"
ssh "$VPS" "cd $UZAK && mkdir -p _yedek/$DMG && for f in $GIDECEK; do [ -f \$f ] && cp \$f _yedek/$DMG/; done" && ok "yedek: $UZAK/_yedek/$DMG"
scp -q $GIDECEK "$VPS:$UZAK/" && ok "gönderildi: $(echo $GIDECEK | wc -w) dosya" || { hata "scp başarısız"; exit 4; }

baslik "5) VPS KAPILARI (Python 3.10 — lokalden KATI)"
if ssh "$VPS" "cd $UZAK && python3 -m py_compile $GIDECEK" 2>&1 | tail -3; then ok "py_compile"; else
  hata "py_compile BAŞARISIZ → geri alınıyor"
  ssh "$VPS" "cd $UZAK && cp _yedek/$DMG/* ."; exit 5
fi
# EKSİK MODÜL KAPISI — "import app" VPS'te 2dk+ sürüyor ve Streamlit uyarıları
# yüzünden yanlış alarm veriyordu (17 Ağu 2026). Yerine: app.py'nin import ettiği
# PROJE modüllerinin VPS'te dosya olarak var olduğunu doğrula — saniyede biter,
# yakalamak istediğimiz asıl hata bu (11 Ağu: sampiyonlar_ligi gönderilmemişti).
MODULLER=$(grep -hoE "^(import|from) [a-z_][a-z_0-9]*" app.py 2>/dev/null | awk '{print $2}' | sort -u)
PROJE=""
for m in $MODULLER; do [ -f "$m.py" ] && PROJE="$PROJE $m.py"; done
if [ -n "$(echo $PROJE)" ]; then
  EKSIK=$(ssh "$VPS" "cd $UZAK && for f in $PROJE; do [ -f \$f ] || echo \$f; done")
  if [ -n "$EKSIK" ]; then
    hata "VPS'te EKSİK modül: $(echo $EKSIK | tr '
' ' ') → geri alınıyor"
    hata "Çözüm: deploy.sh içindeki DOSYALAR listesine ekle, tekrar koş."
    ssh "$VPS" "cd $UZAK && cp _yedek/$DMG/* ."; exit 6
  fi
  ok "modül bütünlüğü ($(echo $PROJE | wc -w) proje modülü VPS'te var)"
fi

baslik "6) RESTART + SAĞLIK"
# 17 Ağu 2026 — İKİ DERS:
# (a) Açılış YAVAŞ: VPS'te app.py'yi import etmek tek başına 150sn+ sürüyor;
#     makine 958MB RAM ve disk I/O'su sıkışıkken 3-4 dk bulabiliyor.
#     Pencere 96sn'ydi → sağlam deploy'u "başarısız" sayıp geri alıyordu.
#     Şimdi 20 x 15sn = 300sn.
# (b) Servisleri AYNI ANDA başlatmak darboğaz yapıyor (hepsi ağır Python).
#     Önce web ayağa kalksın, health 200 olsun; bot ondan SONRA.
ssh "$VPS" "sudo systemctl restart $SERVISLER" && ok "restart: $SERVISLER"
KOD=$(ssh "$VPS" "for i in \$(seq 1 20); do sleep 15; c=\$(curl -s -o /dev/null -w '%{http_code}' $SAGLIK); if [ \"\$c\" = '200' ]; then echo 200; exit 0; fi; done; echo \$c")
if [ "$KOD" = "200" ] && [ "$BOT" = "1" ]; then
  ssh "$VPS" "sudo systemctl restart smr-bot" && ok "restart: smr-bot (web sağlıklı olduktan sonra)"
fi
if [ "$KOD" = "200" ]; then
  ok "health 200 — CANLI"
  ssh "$VPS" "cd $UZAK && ls -1dt _yedek/*/ 2>/dev/null | tail -n +11 | xargs -r rm -rf" 2>/dev/null
  baslik "TAMAM ($(echo $GIDECEK | wc -w) dosya · yedek $DMG)"
  echo "  Geri alma: ssh $VPS \"cd $UZAK && cp _yedek/$DMG/* . && sudo systemctl restart $SERVISLER\""
else
  hata "health $KOD → GERİ ALINIYOR"
  ssh "$VPS" "cd $UZAK && cp _yedek/$DMG/* . && sudo systemctl restart $SERVISLER"
  hata "eski sürüm ayakta. Log: ssh $VPS 'tail -40 ~/smr/logs/patron-radar.log'"
  exit 7
fi
