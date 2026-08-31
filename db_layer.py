# -*- coding: utf-8 -*-
"""
db_layer.py — VERİTABANI ÇEKİRDEĞİ (patron.db şema + temel okuma/yazma)
=======================================================================
app.py bölme projesi Adım 5 (4 Tem 2026) — BİREBİR taşındı (davranış aynı).

İçerik: DB_FILE sabiti · log_error (errors.log) · init_db (tüm şema) ·
MKK yabancı (RSS fetch + sinyal hesabı) · watchlist (yükle/ekle/sil) ·
get_scanner_optimal_windows · get_scenario_ages_batch.

NOT: log_scan_signal / log_erken_radar_signals / backfill_signal_returns
app.py'de KALDI — _compute_signal_features'a (tüm analiz motoru) bağımlılar;
Adım 6-7'de motor taşınınca gelirler. Davranış fotoğrafı: golden_record.py
__DB_LAYER__ bölümü (geçici test DB'sinde şema+watchlist+MKK+senaryo yaşı).
"""
import os
import re
import json
import sqlite3
import urllib.request
import urllib.parse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from signal_policy import ensure_event_schema
from deepening_policy import ensure_deepening_schema

_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")

_ERROR_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "errors.log")


def log_error(where, exc, context=""):
    """Yakalanan istisnayı errors.log'a tek satır olarak yazar.
    where: fonksiyon/konum adı | exc: Exception nesnesi | context: opsiyonel ek bilgi (örn. ticker).
    Loglamanın kendisi asla uygulamayı bozmamalı → tüm yazma try/except içinde."""
    try:
        _ts = datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d %H:%M:%S")
        _ctx = f" [{context}]" if context else ""
        _line = f"{_ts} | {where}{_ctx} | {type(exc).__name__}: {exc}\n"
        with open(_ERROR_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write(_line)
    except Exception:
        pass  # log yazımı başarısızsa sessizce geç — uygulama akışı korunur


DB_FILE = "patron.db"


def init_db():
    # 2 Tem 2026 — DB LOCK fix: app açılışı + fetcher cron + backtest aynı anda
    # patron.db'ye dokununca "database is locked" crash oluyordu (varsayılan bekleme 5sn).
    # timeout=30 → kilitliyse hemen patlamaz, 30sn bekler. WAL KULLANILMADI (patron.db
    # scp ile VPS'e sync ediliyor; WAL yan-dosyası kopyalanmaz → eksik veri riski).
    conn = sqlite3.connect(DB_FILE, timeout=30)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS watchlist (symbol TEXT PRIMARY KEY)')
    c.execute('''CREATE TABLE IF NOT EXISTS scan_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date   TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        scan_type   TEXT NOT NULL,
        score       REAL,
        bias        TEXT DEFAULT 'bullish',
        entry_price REAL,
        stop_level  REAL,
        category    TEXT,
        UNIQUE(scan_date, symbol, scan_type)
    )''')
    # 29 Tem 2026 — aynı kurulumun günlük tekrarlarını yeni sinyal sanmayan olay yaşam döngüsü.
    ensure_event_schema(conn)
    ensure_deepening_schema(conn)
    # 19 Haz 2026 — GOLD MINE VİTRİN LOG (meta-backtest): her Master Scan'de vitrinin gösterdiği
    # top hisseler rank+puanla kaydedilir. Sonra signal_results JOIN ile "vitrin sıralaması
    # gerçekten kazandırdı mı, rank1 > rank10 mu" ölçülür. confluence_count = kaç tarama çakıştı.
    c.execute('''CREATE TABLE IF NOT EXISTS goldmine_log (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date        TEXT NOT NULL,
        symbol           TEXT NOT NULL,
        rank             INTEGER,
        score            REAL,
        scanner          TEXT,
        confluence_count INTEGER,
        unmeasured       INTEGER DEFAULT 0,
        category         TEXT,
        skor_kaynagi     TEXT,
        UNIQUE(scan_date, symbol)
    )''')
    # 27 Ağu 2026 — Gold Mine puanı eski ``ideal_day`` seçimine dayanıyor.
    # Satırın hangi karne yöntemiyle üretildiğini ayırmadan meta-backtest yapmak
    # yanıltıcı olur; eski satırlar NULL kalır ve geriye dönük kayıt bozulmaz.
    _goldmine_cols = {row[1] for row in c.execute('PRAGMA table_info(goldmine_log)').fetchall()}
    if 'skor_kaynagi' not in _goldmine_cols:
        c.execute('ALTER TABLE goldmine_log ADD COLUMN skor_kaynagi TEXT')
    # 31 Ağu 2026 — MAGIC RIBBON 4S GÜNLÜĞÜ (ileri test defteri).
    # Neden ayrı tablo: asıl ölçüm scan_signals'a yazılır ve backfill_signal_returns
    # 1-20 günlük getiriyi ORADAN otomatik hesaplar. Ama scan_signals 4S'e özgü
    # alanları taşımıyor: sinyalin dayandığı KAPANMIŞ bar, tetik yaşı, kaç bardır
    # yukarı olduğu. Bunlar olmadan ileride "YENİ HİZALANMA gerçekten SÜRÜYOR'dan
    # iyi mi", "taze tetik mi eski tetik mi tutuyor" sorulari sorulamaz.
    # Backtest (test dönemi, 5.176 işlem) maruziyet düzeltmesiz al-tut'a -4,9 puan
    # KAYBEDİYOR, düzeltilince +2,1 puan kazanıyor ama hisse bazında %51 (yazı-tura).
    # Yani tarama ÜMİT VAAT EDİYOR ama İSPATLANMADI — bu defter o ispatın verisi.
    c.execute('''CREATE TABLE IF NOT EXISTS magic_ribbon_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date   TEXT NOT NULL,
        bar_time    TEXT,
        symbol      TEXT NOT NULL,
        price       REAL,
        durum       TEXT,
        tetik_yasi  INTEGER,
        yukari_bar  INTEGER,
        fast_line   REAL,
        slow_line   REAL,
        ciro        REAL,
        universe    TEXT,
        engine      TEXT,
        UNIQUE(scan_date, symbol)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_magic_ribbon_sym ON magic_ribbon_log(symbol, scan_date)')
    # 31 Ağu 2026 — Magic Ribbon artık eski 09:30/13:30 4S deposundan değil,
    # TradingView 5dk verisinden kurulan 09:55–14:00 / 14:00–18:10 BIST seans
    # mumlarından okunuyor. İki motorun ileri-test kayıtları karışmasın.
    c.execute('''CREATE TABLE IF NOT EXISTS magic_ribbon_session_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date   TEXT NOT NULL,
        bar_time    TEXT,
        seans       TEXT,
        symbol      TEXT NOT NULL,
        price       REAL,
        durum       TEXT,
        tetik_yasi  INTEGER,
        yukari_bar  INTEGER,
        fast_line   REAL,
        slow_line   REAL,
        ciro        REAL,
        universe    TEXT,
        engine      TEXT,
        UNIQUE(scan_date, symbol)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_magic_ribbon_session_sym ON magic_ribbon_session_log(symbol, scan_date)')
    # 19 Haz 2026 Faz 3 — ANALİZ LOG: hisse analiz edilince birleşik verdict snapshot (kanıt+risk).
    # Sonra forward getiriyle "yüksek kanıt-skorlu + düşük riskli analizler tuttu mu" ölçülür (ürün isabeti).
    c.execute('''CREATE TABLE IF NOT EXISTS analysis_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date     TEXT NOT NULL,
        symbol        TEXT NOT NULL,
        kanit_skoru   REAL,
        guven         REAL,
        master_score  REAL,
        risk_level    TEXT,
        liquidity_tier TEXT,
        manip_risk    TEXT,
        UNIQUE(scan_date, symbol)
    )''')
    # B4-1 (1 Haz 2026): OBV+CMF durumu kalibrasyon için. Eski tablo varsa idempotent ekle.
    try:
        c.execute('ALTER TABLE scan_signals ADD COLUMN obv_status TEXT')
    except sqlite3.OperationalError:
        pass  # kolon zaten var
    # B4-2 (3 Haz 2026): FEATURE SNAPSHOT kolonları — başarılı sinyalleri ayıran
    # özellikleri retroaktif analiz için kaydet. Scanner df'i bu kolonları içerirse yazılır,
    # yoksa NULL kalır (geriye uyumlu). 2-3 hafta veri birikince:
    #   SELECT scan_type, AVG(f_rsi), AVG(f_52h_pos)... FROM scan_signals sg
    #   JOIN signal_results sr ON sg.id=sr.signal_id WHERE sr.hit_10g=1 vs hit_10g=0
    # → başarılı vs başarısız sinyalin feature dağılımı çıkar, kriter tuning veri-tabanlı olur.
    for _alter_col in [
        'ALTER TABLE scan_signals ADD COLUMN f_52h_pos REAL',        # yıllık konum %
        'ALTER TABLE scan_signals ADD COLUMN f_rsi REAL',            # 14g RSI
        'ALTER TABLE scan_signals ADD COLUMN f_cmf_dual TEXT',       # 5g/20g CMF state (strong_pos/turning_up vs)
        'ALTER TABLE scan_signals ADD COLUMN f_omi_sigma REAL',      # OBV Momentum Index σ
        'ALTER TABLE scan_signals ADD COLUMN f_squeeze_days INTEGER',# BB ⊂ Keltner kaç gündür
        'ALTER TABLE scan_signals ADD COLUMN f_vp_shape TEXT',       # Akümülasyon/Dağıtım/Denge
        'ALTER TABLE scan_signals ADD COLUMN f_master_score REAL',   # Master Score (0-100)
        # 6 Haz 2026 — POC tabanlı 3 backtest doğrulamalı flag (0/1, NULL if veri yok):
        'ALTER TABLE scan_signals ADD COLUMN f_poc_magnet INTEGER',      # 20g POC altı + ana trend yukarı + stretched
        'ALTER TABLE scan_signals ADD COLUMN f_poc_confluence INTEGER', # 20g/60g/250g POC spread < %2
        'ALTER TABLE scan_signals ADD COLUMN f_avwap_test_zone INTEGER',# 52H zirve/dip aVWAP %2 içinde test
        # 6 Haz 2026 Oturum 18+ — Master Score breakdown 4 alt skor (component winners/losers analizi için):
        'ALTER TABLE scan_signals ADD COLUMN f_ms_trend REAL',          # 0-100
        'ALTER TABLE scan_signals ADD COLUMN f_ms_momentum REAL',       # 0-100
        'ALTER TABLE scan_signals ADD COLUMN f_ms_ict REAL',            # 0-100
        'ALTER TABLE scan_signals ADD COLUMN f_ms_radar2 REAL',         # 0-100
        # 8 Haz 2026 Oturum 19 — Dual-window feature genişlemesi (CMF disiplinini cum_delta + RSI'ya yay):
        'ALTER TABLE scan_signals ADD COLUMN f_cum_delta_dual TEXT',    # 5g/20g cum_delta state (7 state, CMF ile aynı kalıp)
        'ALTER TABLE scan_signals ADD COLUMN f_rsi_dual TEXT',          # RSI(5)/RSI(14) state (7 state — overbought/oversold/early/cooling/dip_recovery)
        # 9 Haz 2026 Oturum 20 — Breakout state (Kibar Type 1): 0 forming · 1 testing · 2 breakout · 3 breakaway gap
        'ALTER TABLE scan_signals ADD COLUMN f_breakout_state INTEGER',
        # 9 Haz 2026 Oturum 20 son — SMC kurumsal 4 yeni flag (BACKTEST: Eylül 2026 ortası
        # signal_returns × bu flag'ler JOIN ile her birinin BIST hit/ret katkısı ölçülecek):
        'ALTER TABLE scan_signals ADD COLUMN f_at_vwap_minus_2sigma INTEGER',  # fiyat -2σ VWAP zonunda (≤ ±%0.5)
        'ALTER TABLE scan_signals ADD COLUMN f_at_y_open INTEGER',             # Y-Open'a yakın (±%2 içinde)
        'ALTER TABLE scan_signals ADD COLUMN f_near_ifvg INTEGER',             # inverse FVG'ye ±%2 içinde
        'ALTER TABLE scan_signals ADD COLUMN f_breaker_block_active INTEGER',  # aktif BB zonunda ±%2 içinde
        # 9 Haz 2026 Oturum 20 — KURUMSAL TAKİP (TEFAS + KAP) — 8 STRONG flag
        # Eşikler: ≥3 fon (AUM≥100mn, pozisyon≥%1.5), buyback float≥%1, threshold %5/%10/%25 üstü
        'ALTER TABLE scan_signals ADD COLUMN f_tefas_konsensus_alim INTEGER',  # ≥3 fon 5g pozitif (STRONG)
        'ALTER TABLE scan_signals ADD COLUMN f_tefas_konsensus_satim INTEGER', # ≥3 fon 5g negatif (STRONG)
        'ALTER TABLE scan_signals ADD COLUMN f_tefas_yeni_giris INTEGER',      # ≥2 büyük fon ilk pozisyon
        'ALTER TABLE scan_signals ADD COLUMN f_buyback_aktif INTEGER',         # yeni program + 5g icra
        'ALTER TABLE scan_signals ADD COLUMN f_buyback_dip_aliyor INTEGER',    # 52H dip ≤%10 + alım
        'ALTER TABLE scan_signals ADD COLUMN f_threshold_asildi INTEGER',      # %5/%10/%25 eşik aşımı (yukarı)
        'ALTER TABLE scan_signals ADD COLUMN f_insider_first_buy INTEGER',     # 6ay sonra ilk içerden alım
        'ALTER TABLE scan_signals ADD COLUMN f_kurumsal_anchor INTEGER',       # convergence (3+ aynı yön)
        # 18 Haz 2026 — Tavan Motoru (60g 1131 örnek backtest, skor ≥150 hit %11.24 random×3.44):
        # Master Scan adım son'da çalışır, scan_signals'a iki scan_type olarak yazılır:
        #   tavan_alarm = skor ≥150 (günde ~12 hisse, en yüksek precision)
        #   tavan_top30 = sıra ≤30 (günde 30 hisse, geniş izleme)
        # Eylül 2026 ortası signal_returns × bu flag'ler JOIN → gerçek hit/ret katkısı
        # ölçülür, kalibrasyon DB tabanlı güncellenir.
        'ALTER TABLE scan_signals ADD COLUMN f_tavan_skor REAL',           # tavan motoru ham skor
        'ALTER TABLE scan_signals ADD COLUMN f_tavan_kat TEXT',            # A/C/E/D (dominant kalıp)
        'ALTER TABLE scan_signals ADD COLUMN f_tavan_confluence_n INTEGER',# 50+ skor alan kalıp sayısı
        # 18 Haz 2026 — Risk profili (global banka 3'lüsü): beta + drawdown + HV oranı
        # Eylül 2026 ortası signal_returns JOIN ile beta/DD seviyelerine göre hit/ret farklılaşması ölç.
        'ALTER TABLE scan_signals ADD COLUMN f_beta_xu100 REAL',           # 60g XU100 beta
        'ALTER TABLE scan_signals ADD COLUMN f_dd_zirveden REAL',          # 1y zirveden % drawdown
        'ALTER TABLE scan_signals ADD COLUMN f_hv_oran REAL',              # 20g/60g HV oranı (sıkışma teyit)
        'ALTER TABLE scan_signals ADD COLUMN f_skew_60g REAL',             # 60g günlük getiri asimetrisi (şok yön bias)
        # 29 Haz 2026 — Kurumsal-tarz faktörler (kesitsel sıralama READ-time panelde):
        'ALTER TABLE scan_signals ADD COLUMN f_mom_12_1 REAL',       # 12-1 ay momentum %
        'ALTER TABLE scan_signals ADD COLUMN f_vol_60g REAL',        # 60g yıllık vol % (düşük-vol faktörü)
        'ALTER TABLE scan_signals ADD COLUMN f_sharpe_mom REAL',     # riske-göre momentum (Sharpe-benzeri)
        'ALTER TABLE scan_signals ADD COLUMN f_trend_persist REAL',  # SMA50 üstü kalma % (trend kalıcılığı)
        # 19 Haz 2026 audit — eskiden loglanmayan "kör" skorlar (Sentiment/ICT-model/SmartMoney).
        # Prominently gösteriliyordu ama backtest edilemiyordu → Temmuz'da getiriyle test için loglanır.
        'ALTER TABLE scan_signals ADD COLUMN f_sentiment_score REAL',
        'ALTER TABLE scan_signals ADD COLUMN f_ict_model REAL',
        'ALTER TABLE scan_signals ADD COLUMN f_smart_money_score REAL',
        # 19 Haz 2026 Faz 1 — likidite + manipülasyon kalkanı (BIST koruması)
        'ALTER TABLE scan_signals ADD COLUMN f_adv_tl REAL',           # 20g ort günlük TL hacim (milyon)
        'ALTER TABLE scan_signals ADD COLUMN f_liquidity_tier TEXT',   # yüksek/orta/düşük
        'ALTER TABLE scan_signals ADD COLUMN f_manip_risk TEXT',       # yok/orta/yüksek
        # 10 Haz 2026 Oturum 20 — MFI (Money Flow Index) — hacim-ağırlıklı RSI
        # 7 state dual-window (RSI_dual ile aynı kalıp):
        #   overbought_both / oversold_both / early_overbought / early_oversold /
        #   cooling_smart_exit / smart_money_recovery / neutral
        'ALTER TABLE scan_signals ADD COLUMN f_mfi_dual TEXT',
        # ELIT confluence flag — RSI ve MFI aynı yönde divergence verirse
        # tek başına RSI div'inin teyit gücü yetmez; MFI hacim teyidi eklenince
        # "akıllı para çekildi" hipotezi güçlenir. Wyckoff effort-vs-result'un
        # algoritmik karşılığı. Nadir ama TIER_1 seviyesinde bekleniyor.
        'ALTER TABLE scan_signals ADD COLUMN f_rsi_mfi_bouquet INTEGER',
        # 10 Haz 2026 Oturum 20 — MKK YABANCI NET ALIŞ (İş Yatırım RSS feed)
        # Kaynak: arastirma.isyatirim.com.tr — günlük rapor, top 3 giriş/çıkış + streak
        # Yabancı kurumsal akış = GERÇEK smart money izi (TEFAS yerli, MKK yabancı)
        'ALTER TABLE scan_signals ADD COLUMN f_yabanci_giris INTEGER',         # son 5g top giriş listesinde
        'ALTER TABLE scan_signals ADD COLUMN f_yabanci_cikis INTEGER',         # son 5g top çıkış listesinde
        'ALTER TABLE scan_signals ADD COLUMN f_yabanci_streak INTEGER',        # 3+ gün üst üste yabancı artış
        'ALTER TABLE scan_signals ADD COLUMN f_yabanci_anchor INTEGER',        # giriş + streak çakışması (ELIT)
        # 10 Haz 2026 Oturum 20 — Relative OBV (hisse OBV trendi vs endeks OBV trendi)
        # 5 state: outperform_strong/outperform_mild/inline/underperform_mild/underperform_strong
        # Mansfield RS'in hacim katmanı — gerçek akıllı para ayrışması
        'ALTER TABLE scan_signals ADD COLUMN f_rel_obv_state TEXT',
        # ELIT: outperform_strong veya underperform_strong = karşı yönde ayrışma → 1
        'ALTER TABLE scan_signals ADD COLUMN f_rel_obv_divergence INTEGER',
        # 10 Haz 2026 Oturum 20 — YAPISAL vs TACTICAL skor ayrımı
        # Smart money sinyalleri iki ölçeğe ayrıştırılır:
        # YAPISAL (haftalar/aylar) vs TACTICAL (saatler/günler).
        # Hangisi hit oranını daha çok belirliyor backtest ile öğrenilecek.
        'ALTER TABLE scan_signals ADD COLUMN f_smart_structural_score REAL',  # 0-100
        'ALTER TABLE scan_signals ADD COLUMN f_smart_tactical_score REAL',    # 0-100
        # 10 Haz 2026 Oturum 20 — Up/Down Volume Ratio (Wyckoff Effort-vs-Result)
        # UDVR = up_volume / down_volume, period bazlı.
        # 5 state: strong_buyer/buyer/balanced/seller/strong_seller
        'ALTER TABLE scan_signals ADD COLUMN f_udvr_20g REAL',                 # 20g UDVR oranı
        'ALTER TABLE scan_signals ADD COLUMN f_udvr_state TEXT',               # 5 state
        # Wyckoff Climax detection — fiyat extremde + UDVR ters yönde
        # 'climax_top' (fiyat tepede + UDVR<0.8 = smart money çekiliyor)
        # 'climax_bottom' (fiyat dipte + UDVR>1.25 = smart money giriyor)
        'ALTER TABLE scan_signals ADD COLUMN f_udvr_climax TEXT',
        # 10 Haz 2026 Oturum 20 — Force Index Dual (Elder Triple Screen)
        # 7 state: strong_pos/strong_neg/turning_up/turning_down/pos/neg/neutral
        # Divergence: bullish (fiyat LL + FI HL) veya bearish (fiyat HH + FI LH)
        'ALTER TABLE scan_signals ADD COLUMN f_force_index_dual TEXT',
        'ALTER TABLE scan_signals ADD COLUMN f_force_index_divergence TEXT',
        # 12 Haz 2026 — Tek-mum dominance bitmask (path-aware koruma)
        # Bit 0=OBV(5g), 1=CumDelta(5g), 2=CMF(5g), 3=RSI(5), 4=MFI(5).
        # Bugünkü mumun ilgili dual-window deltasının >%60'ını oluşturduğu durumlar
        # set edilir. AI prompt ve OBV panelinde "teyit bekleniyor" rozetine bağlanır.
        'ALTER TABLE scan_signals ADD COLUMN f_spike_dominance INTEGER',
        # 17 Tem 2026 EKRAN REFORMU 2c — SFP (Swing Failure Pattern / tuzak) flag'leri.
        # PA panelinde gösteriliyordu ama hiç loglanmıyordu. Eylül 2026 karnesi:
        # signal_returns JOIN → "BIST'te tuzak sinyali dönüş öngörüyor mu?" ölçülecek.
        # Tek kaynak: ict_core.compute_sfp_flags (PA-DNA da aynı fonksiyonu kullanır).
        'ALTER TABLE scan_signals ADD COLUMN f_sfp_bull INTEGER',   # dip süpürüldü + üstünde kapanış
        'ALTER TABLE scan_signals ADD COLUMN f_sfp_bear INTEGER',   # tepe süpürüldü + altında kapanış
    ]:
        try:
            c.execute(_alter_col)
        except sqlite3.OperationalError:
            pass  # kolon zaten var
    # ── Günlük getiri takip tablosu (1-20 işlem günü, backtest için) ─────────
    # Her scan_signals satırına karşılık 20 satır — day_offset 1..20.
    # close_price / return_pct başta NULL; backfill_signal_returns() doldurur.
    c.execute('''CREATE TABLE IF NOT EXISTS signal_returns (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id   INTEGER NOT NULL,
        scan_type   TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        signal_date TEXT NOT NULL,
        entry_price REAL,
        day_offset  INTEGER NOT NULL,
        close_price REAL,
        return_pct  REAL,
        category    TEXT,
        UNIQUE(signal_id, day_offset)
    )''')
    # ── KURUMSAL TAKİP cache tabloları (9 Haz 2026 Oturum 20) ──────────────
    # TEFAS portföy snapshot: günlük fetch, hisse bazlı SQL sorgu için
    c.execute('''CREATE TABLE IF NOT EXISTS tefas_holdings (
        snapshot_date TEXT NOT NULL,    -- YYYY-MM-DD
        fund_code     TEXT NOT NULL,    -- ör. AAK, GZL, PYL
        fund_aum_mn   REAL,             -- fonun AUM, milyon TL
        stock_symbol  TEXT NOT NULL,    -- BIST hisse kodu (örn. XBANK)
        weight_pct    REAL,             -- fon portföyündeki yüzde
        PRIMARY KEY (snapshot_date, fund_code, stock_symbol)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_tefas_sym ON tefas_holdings(stock_symbol, snapshot_date)')
    # KAP duyuru cache: buyback + ownership değişiklikleri
    # NOT: SQLite PRIMARY KEY içinde COALESCE/expression kabul etmez.
    # actor kolonunu NOT NULL DEFAULT '' yapıp doğrudan PK'ye koyduk.
    c.execute('''CREATE TABLE IF NOT EXISTS kap_events (
        event_date     TEXT NOT NULL,
        symbol         TEXT NOT NULL,
        event_type     TEXT NOT NULL,   -- 'buyback' | 'ownership_change' | 'insider_buy'
        event_subtype  TEXT,            -- 'new_program' | 'threshold_5pct' vs
        amount_tl      REAL,            -- tutar (TL)
        amount_lots    REAL,            -- adet (varsa)
        price          REAL,            -- ortalama fiyat
        actor          TEXT NOT NULL DEFAULT '',  -- alıcı/satıcı (yönetici/holding adı)
        meta_json      TEXT,            -- ham JSON detay
        PRIMARY KEY (event_date, symbol, event_type, actor)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_kap_sym ON kap_events(symbol, event_date)')

    # MKK Yabancı Net Alış cache (İş Yatırım RSS feed kaynağı)
    c.execute('''CREATE TABLE IF NOT EXISTS mkk_yabanci (
        report_date  TEXT NOT NULL,    -- YYYY-MM-DD
        symbol       TEXT NOT NULL,    -- BIST kodu
        direction    TEXT NOT NULL,    -- 'in' | 'out' | 'streak'
        bps_change   REAL,             -- baz puan (sadece in/out için)
        streak_days  INTEGER,          -- üst üste artış günü (sadece streak için)
        PRIMARY KEY (report_date, symbol, direction)
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mkk_sym ON mkk_yabanci(symbol, report_date)')

    conn.commit()
    conn.close()


def _fetch_mkk_yabanci_rss(max_days=30):
    """MKK yabancı net alış verisini İş Yatırım RSS feed'inden çeker.

    Kaynak: https://arastirma.isyatirim.com.tr/category/gunluk-raporlar/gunluk-yabanci-oranlari/feed/

    Her gün için 3 tip kayıt:
      - top 3 yabancı GİRİŞ (positif bps)
      - top 3 yabancı ÇIKIŞ (negatif bps)
      - top 5 SÜREKLI ARTIŞ streak (N gün üst üste)

    Returns: (success_count, fail_count)
    """
    import requests as _requests
    import re as _re
    import xml.etree.ElementTree as _ET
    from datetime import datetime as _dt

    _url = 'https://arastirma.isyatirim.com.tr/category/gunluk-raporlar/gunluk-yabanci-oranlari/feed/'

    try:
        _r = _requests.get(_url, timeout=15,
                           headers={'User-Agent': 'Mozilla/5.0 (compatible; PatronRadar/1.0)'})
        if _r.status_code != 200:
            return (0, 1)
        _root = _ET.fromstring(_r.text.encode('utf-8'))
    except Exception as _ex:
        try: log_error("mkk_rss_fetch", _ex)
        except Exception: pass
        return (0, 1)

    _ns = {'content': 'http://purl.org/rss/1.0/modules/content/'}
    _items = _root.findall('.//item')
    if not _items:
        return (0, 1)

    # Parse pattern'leri
    _pat_pair_bps = _re.compile(r'([A-Z]{2,7}):(-?\d+(?:[.,]\d+)?)\s*bps')
    _pat_pair_gun = _re.compile(r'([A-Z]{2,7}):(\d+)\s*g[üu]n')
    _pat_title_date = _re.compile(r'(\d{2})/(\d{2})/(\d{4})')

    _conn = sqlite3.connect(DB_FILE, timeout=30)
    _c = _conn.cursor()
    _success = 0; _fail = 0

    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        # bs4 yok — text temizliği basit regex ile
        _BS = None

    for _item in _items[:max_days]:
        try:
            _title = _item.findtext('title', default='')
            _m_date = _pat_title_date.search(_title)
            if not _m_date:
                _fail += 1; continue
            _d, _m, _y = _m_date.groups()
            _date_str = f'{_y}-{_m}-{_d}'

            _cont_el = _item.find('content:encoded', _ns)
            _cont = _cont_el.text if _cont_el is not None else ''
            if _BS is not None:
                _text = _BS(_cont, 'html.parser').get_text(separator=' ', strip=True)
            else:
                _text = _re.sub(r'<[^>]+>', ' ', _cont)
            _text = ' '.join(_text.split())

            # Tüm bps eşleşmeleri sırayla — ilk 3 artış, sonra 3 azalış
            _all_bps = _pat_pair_bps.findall(_text)
            _inc = _all_bps[:3]
            _dec = _all_bps[3:6]
            _streak = _pat_pair_gun.findall(_text)[:5]

            # DB'ye yaz
            for _sym, _bps_v in _inc:
                try:
                    _bf = float(str(_bps_v).replace(',', '.'))
                    _c.execute('''INSERT OR REPLACE INTO mkk_yabanci
                                  (report_date, symbol, direction, bps_change, streak_days)
                                  VALUES (?, ?, 'in', ?, NULL)''',
                               (_date_str, _sym.upper(), _bf))
                except Exception: pass
            for _sym, _bps_v in _dec:
                try:
                    _bf = float(str(_bps_v).replace(',', '.'))
                    _c.execute('''INSERT OR REPLACE INTO mkk_yabanci
                                  (report_date, symbol, direction, bps_change, streak_days)
                                  VALUES (?, ?, 'out', ?, NULL)''',
                               (_date_str, _sym.upper(), _bf))
                except Exception: pass
            for _sym, _gun_v in _streak:
                try:
                    _gf = int(_gun_v)
                    _c.execute('''INSERT OR REPLACE INTO mkk_yabanci
                                  (report_date, symbol, direction, bps_change, streak_days)
                                  VALUES (?, ?, 'streak', NULL, ?)''',
                               (_date_str, _sym.upper(), _gf))
                except Exception: pass

            _success += 1
        except Exception:
            _fail += 1

    _conn.commit(); _conn.close()
    return (_success, _fail)


def _compute_mkk_yabanci_signals(ticker: str) -> dict:
    """MKK yabancı net alış cache'inden 4 sinyali hesaplar.

    f_yabanci_giris   : son 5g top giriş listesinde + bps>0
    f_yabanci_cikis   : son 5g top çıkış listesinde + bps<0
    f_yabanci_streak  : son raporda 3+ gün üst üste artış streak
    f_yabanci_anchor  : (giriş + streak) çakışması → ELIT smart money
    streak_days       : en yüksek streak gün sayısı (panel rozeti — 10 Tem 2026)
    in_days / out_days: son 5 raporda kaç farklı gün giriş/çıkış listesinde

    BIST hissesi değilse tüm None döner.
    """
    out = {
        'f_yabanci_giris': None,
        'f_yabanci_cikis': None,
        'f_yabanci_streak': None,
        'f_yabanci_anchor': None,
        'streak_days': 0,
        'in_days': 0,
        'out_days': 0,
    }
    try:
        _sym = ticker.upper().replace('.IS', '')
        if any(_sym.startswith(p) for p in ('XU', 'XB', 'XT', 'XY', '^')) or '=' in _sym or '-' in _sym:
            return out

        conn = sqlite3.connect(DB_FILE, timeout=30)
        c = conn.cursor()
        # Son 5 işlem günü
        c.execute('''SELECT direction, bps_change, streak_days, report_date FROM mkk_yabanci
                     WHERE symbol = ?
                       AND report_date >= date('now', '-5 day')
                     ORDER BY report_date DESC''', (_sym,))
        _rows = c.fetchall()
        conn.close()

        _has_in = 0
        _has_out = 0
        _max_streak = 0
        _in_dates = set()
        _out_dates = set()
        for _dir, _bps, _streak, _rd in _rows:
            if _dir == 'in' and _bps and _bps > 0:
                _has_in = 1
                _in_dates.add(_rd)
            elif _dir == 'out' and _bps and _bps < 0:
                _has_out = 1
                _out_dates.add(_rd)
            elif _dir == 'streak' and _streak:
                _max_streak = max(_max_streak, int(_streak))

        out['f_yabanci_giris']  = _has_in
        out['f_yabanci_cikis']  = _has_out
        out['f_yabanci_streak'] = 1 if _max_streak >= 3 else 0
        out['f_yabanci_anchor'] = 1 if (_has_in == 1 and _max_streak >= 3) else 0
        out['streak_days'] = _max_streak
        out['in_days']  = len(_in_dates)
        out['out_days'] = len(_out_dates)
    except Exception as _ex:
        try: log_error("mkk_yabanci_signals", _ex, ctx={'ticker': ticker})
        except Exception: pass
    return out


def load_watchlist_db():
    # 20 Tem 2026 — AÇILIŞ ÇÖKME FİX: gece backtest'i (backtest_runner) patron.db'yi uzun süre
    # kilitleyince bu ÇIPLAK okuma "database is locked" ile TÜM uygulamayı açılışta çökertiyordu
    # (init_db "atla, devam" derken watchlist okuması sert patlıyordu). Artık kilitliyse birkaç kez
    # dener, olmazsa BOŞ döner → uygulama kayıtlı-liste-yok gibi normal açılır. WAL bilinçli KAPALI
    # (scp sync yan-dosya riski, bkz init_db notu), o yüzden çözüm dayanıklı-okuma.
    import time
    for _attempt in range(3):
        try:
            conn = sqlite3.connect(DB_FILE, timeout=30)
            try:
                data = conn.execute('SELECT symbol FROM watchlist').fetchall()
            finally:
                conn.close()
            return [x[0] for x in data]
        except sqlite3.OperationalError:
            time.sleep(1)
    return []   # kilit sürüyor → boş liste, uygulama açılır (çökme yok)


def add_watchlist_db(symbol):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO watchlist (symbol) VALUES (?)', (symbol,))
        conn.commit()
    except sqlite3.IntegrityError: 
        pass
    conn.close()


def remove_watchlist_db(symbol):
    conn = sqlite3.connect(DB_FILE, timeout=30)
    c = conn.cursor()
    c.execute('DELETE FROM watchlist WHERE symbol = ?', (symbol,))
    conn.commit()
    conn.close()


def get_scanner_optimal_windows():
    """
    signal_returns tablosunu analiz eder. Her scan_type için:
    - Gün bazlı ortalama getiri ve hit rate (return > 0)
    - Peak gün: avg_return × hit_rate kompozit skorun en yüksek olduğu gün
    - Minimum 5 sinyal şartı (az örnekle yanıltıcı sonuç olmasın)

    Returns: {
      scan_type: {
        "peak_day":    N,        # optimal tutma süresi (işlem günü)
        "peak_return": X.XX,     # o günde ort. getiri %
        "hit_rate":    YY.Y,     # o günde hit rate %
        "n_signals":   Z,        # toplam değerlendirilen sinyal sayısı
        "daily": {               # tüm günlerin özeti
          1: {"avg_return": x, "hit_rate": y, "n": z},
          ...20
        }
      }
    }
    """
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30)
        df = pd.read_sql('''
            SELECT scan_type, day_offset, return_pct
            FROM signal_returns
            WHERE return_pct IS NOT NULL
        ''', conn)
        conn.close()
    except Exception:
        return {}

    if df.empty:
        return {}

    results = {}
    for scan_type, grp in df.groupby('scan_type'):
        # Kaç benzersiz sinyal var? (day_offset=1 satır sayısını say)
        n_signals = int((grp['day_offset'] == 1).sum())
        if n_signals < 5:
            continue  # Yetersiz veri

        best_score  = -999.0
        best_day    = None
        daily_stats = {}

        for day_offset, day_grp in grp.groupby('day_offset'):
            rets = day_grp['return_pct'].dropna()
            if len(rets) < 3:
                continue
            avg_ret  = float(rets.mean())
            hit_rate = float((rets > 0).mean())
            # Kompozit skor: pozitif getiri × tutarlılık
            composite = avg_ret * hit_rate
            daily_stats[int(day_offset)] = {
                "avg_return": round(avg_ret, 2),
                "hit_rate":   round(hit_rate * 100, 1),
                "n":          int(len(rets))
            }
            if composite > best_score:
                best_score = composite
                best_day   = int(day_offset)

        if best_day is not None:
            results[scan_type] = {
                "peak_day":    best_day,
                "peak_return": daily_stats[best_day]["avg_return"],
                "hit_rate":    daily_stats[best_day]["hit_rate"],
                "n_signals":   n_signals,
                "daily":       daily_stats
            }

    return results


def get_scenario_lifecycle_batch(pairs, max_lookback=180):
    """Erken Radar senaryolarının olay başlangıcını ve olay gününü toplu döndürür."""
    if not pairs:
        return {}
    result = {}
    conn = None
    try:
        conn = sqlite3.connect(DB_FILE, timeout=30)
        ensure_event_schema(conn)
        for sym, sid in pairs:
            sym_clean = str(sym or '').replace('.IS', '')
            if not sym_clean or not sid:
                result[(sym, sid)] = {
                    'event_id': None, 'first_seen': None, 'last_seen': None,
                    'event_day': 0, 'is_new': False,
                }
                continue
            row = conn.execute(
                """
                SELECT event_id, event_start_date, scan_date, event_day, is_event_start
                FROM scan_signals
                WHERE symbol=? AND scan_type=?
                  AND scan_date >= date('now', ?)
                ORDER BY scan_date DESC, id DESC
                LIMIT 1
                """,
                (sym_clean, f"er_{sid}", f"-{int(max_lookback)} days"),
            ).fetchone()
            if row:
                result[(sym, sid)] = {
                    'event_id': row[0],
                    'first_seen': row[1] or row[2],
                    'last_seen': row[2],
                    'event_day': int(row[3] or 1),
                    'is_new': bool(row[4]),
                }
            else:
                result[(sym, sid)] = {
                    'event_id': None, 'first_seen': None, 'last_seen': None,
                    'event_day': 0, 'is_new': False,
                }
    except Exception:
        for pair in pairs:
            result.setdefault(pair, {
                'event_id': None, 'first_seen': None, 'last_seen': None,
                'event_day': 0, 'is_new': False,
            })
    finally:
        if conn is not None:
            conn.close()
    return result


def get_scenario_ages_batch(pairs, max_lookback=180, include_details=False):
    """
    Toplu senaryo yaşam döngüsü hesabı.
    Varsayılan dönüş: {(symbol, scenario_id): event_day_int}
    include_details=True: ilk oluşum tarihi dahil ayrıntılı yaşam döngüsü.

    event_day takvim günü değildir; taramanın çalıştığı ardışık seanslarda aynı
    kurulumun kaçıncı kez görüldüğüdür. Hafta sonu ve tatiller sayacı bozmaz.
    """
    details = get_scenario_lifecycle_batch(pairs, max_lookback=max_lookback)
    if include_details:
        return details
    return {pair: int(info.get('event_day') or 0) for pair, info in details.items()}
