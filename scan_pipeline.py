# -*- coding: utf-8 -*-
"""scan_pipeline.py — MASTER SCAN BORU HATTI (Adim 7c, 10 Tem 2026)
========================================================================
app.py bolme — feature hesabi + DB loglama + toplu tarama donguleri BIREBIR
tasindi (davranis degisikligi YOK, sadece adres). Icerik: _compute_signal_features
(feature dugumu) + log_scan_signal/log_erken_radar + backfill_signal_returns +
scan_*_batch aileleri + chart_patterns/golden_pattern_agent/golden_trio.
app.py'de kalanlar (UI-yapisik): scan_para_akisi_liderleri (tavan ailesi),
log_analysis_snapshot (kanit paneli), log_goldmine_selection (goldmine vitrini).
Fotograf: golden_record pipe_* + __PIPELINE__ hedefleri (sifir fark).
"""
import os
import time
import sqlite3
import logging
import concurrent.futures
from pathlib import Path
import numpy as np
import pandas as pd
import pytz
import streamlit as st
import yfinance as yf
from datetime import datetime, timedelta

import pattern_core
from data_layer import (CACHE_DIR, _apply_split_adjustments, _normalize_bist_ticker,
                        fetch_index_data_cached, get_batch_data_cached, get_benchmark_data,
                        get_safe_historical_data)
from db_layer import DB_FILE, _compute_mkk_yabanci_signals, log_error
from patron_db_guard import database_write_lock
from indicators import (_harmonik_52h_strength, _spike_dom_ratio, calculate_anchored_vwap,
                        calculate_multi_tf_pocs, compute_cmf, compute_force_index_dual,
                        compute_mfi, compute_relative_obv_state, compute_updown_volume_ratio,
                        detect_darvas_box, find_smart_sr_levels)
from scanners import (_detect_double_bottom, _detect_double_top, _detect_wedge,
                      _is_index_symbol, ERKEN_RADAR_SCENARIOS,
                      _nadir_firsat_single_fast, _validate_cup_shape, _validate_tobo_shape,
                      calculate_guclu_donus_adaylari, calculate_prelaunch_bos,
                      evaluate_erken_radar, process_single_accumulation,
                      process_single_ict_setup, process_single_radar1, process_single_radar2)
from rsi_divergence_scanner import (detect_wilder_positive_divergence,
                                    empty_result_frame as _empty_wilder_result_frame)
from deepening_policy import (
    b11_pilot_profile,
    build_leadership_lifecycle,
    ensure_deepening_schema,
    leadership_profile,
    upsert_rsi_journey,
)
from ict_core import (calculate_harmonic_confluence, calculate_ict_deep_analysis,
                      calculate_minervini_sepa, compute_sfp_flags)
from scoring_core import (_compute_risk_profile, _compute_smc_elements, _detect_breakout_state,
                          _liquidity_manip, calculate_master_score, calculate_sentiment_score,
                          calculate_smart_money_score, compute_smart_money_split_scores)
from signal_policy import (assign_event_metadata_for_date, ensure_event_schema,
                           register_scan_run, resolve_next_open_entry)
from stp_uyanis_core import calculate_stp_uyanis_status
from market_cap_cache import load_market_cap_map

_TZ_ISTANBUL = pytz.timezone("Europe/Istanbul")


# ===============================================================
# 12 Haz 2026 — Tek-mum dominance helper (path-aware koruma)
# Dual-window state'ler (OBV/CMF/CumDelta/RSI/MFI) bugün >X% katkıyla
# şişirilmişse "teyit bekleniyor" olarak işaretlemek için kullanılır.
# Aynı işaret + bugünkü delta / pencere deltası > eşik.
# ===============================================================
SPIKE_DOM_THRESHOLD = 0.60  # bugün >%60 ise dominant

_SCAN_LOG_DISABLED = False   # tek-hisse canlı tarama sırasında True → DB'ye yazma (backtest kirlenmesin)

# 4 Tem 2026 (5c): global bool yerine TICKER-BAZLI atlama seti. Eski bool tüm
# yazımı kapatıyordu → eşzamanlı başka oturumun Master Scan logları da sessizce
# kaçabiliyordu. Set sadece canlı-tek-hisse bakılan ticker'ın satırını süzer.
_SCAN_LOG_SKIP = set()

# ── HARMONİK 52H GÜÇ FİLTRESİ (19 Haz 2026 — iki-rejim backtest kanıtlı) ──
# Mayıs(boğa)+Haziran(ayı) 441 sinyal segmentasyonu: hisse 52H zirvesine yakınken
# (52H konum ≥%60) harmonik confluence Mayıs %90 / Haziran %100 isabet; dibe yakınken
# (<%40) Mayıs %49 / Haziran %35. +53 puan, HER İKİ rejimde de geçerli (rejim değil,
# kurulum kalitesi). Bu yüzden güçlü/zayıf işaretle + güçlüyü öne sırala.
# HARMONIK_HIDE_WEAK=True → zayıf (52H<%40) sinyaller tamamen gizlenir (geri al: False).
HARMONIK_HIDE_WEAK = False


def _terazi_rsi14_value(df):
    """Terazi ve tam feature yolu için tek RSI-14 formülü."""
    try:
        c = df['Close']
        delta = c.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(float(rsi.iloc[-1]), 1)
    except Exception:
        return None


def _terazi_cmf_dual_state(df):
    """Terazi ve tam feature yolu için tek CMF çift-pencere durumu."""
    try:
        cmf5 = float(compute_cmf(df, period=5))
        cmf20 = float(compute_cmf(df, period=20))
        if cmf5 > 0.05 and cmf20 > 0.05:
            return 'strong_pos'
        if cmf5 < -0.05 and cmf20 < -0.05:
            return 'strong_neg'
        if cmf5 > 0 and cmf20 < 0:
            return 'turning_up'
        if cmf5 < 0 and cmf20 > 0:
            return 'turning_down'
        if cmf20 > 0.05:
            return 'pos'
        if cmf20 < -0.05:
            return 'neg'
        return 'neutral'
    except Exception:
        return None


def _compute_terazi_signal_features(ticker: str) -> dict:
    """Terazi'nin gerçekten tükettiği dört ham girdiyi tek fotoğraftan üretir."""
    ticker = _normalize_bist_ticker(ticker)
    out = {
        'f_rsi': None, 'f_cmf_dual': None,
        'f_yabanci_streak': None,
        'f_sfp_bull': None, 'f_sfp_bear': None,
    }
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 60:
            return out
        out['f_rsi'] = _terazi_rsi14_value(df)
        out['f_cmf_dual'] = _terazi_cmf_dual_state(df)
        try:
            out['f_sfp_bull'], out['f_sfp_bear'] = compute_sfp_flags(df)
        except Exception:
            pass
        try:
            _mkk_y = _compute_mkk_yabanci_signals(ticker)
            if _mkk_y.get('f_yabanci_streak') is not None:
                out['f_yabanci_streak'] = _mkk_y['f_yabanci_streak']
        except Exception:
            pass
    except Exception:
        pass
    return out


def _compute_signal_features(ticker: str) -> dict:
    """Sinyal anında hisseye özel 7 feature snapshot. Cache 10dk."""
    ticker = _normalize_bist_ticker(ticker)   # 24 Haz: çıplak sembol (EREGL→EREGL.IS) — Yahoo 404 selini/donmayı önler
    out = {
        'f_52h_pos': None, 'f_rsi': None, 'f_cmf_dual': None,
        'f_omi_sigma': None, 'f_squeeze_days': None,
        'f_vp_shape': None, 'f_master_score': None,
        # 19 Haz 2026 audit — eskiden loglanmayan "kör" skorlar
        'f_sentiment_score': None, 'f_ict_model': None, 'f_smart_money_score': None,
        # 19 Haz 2026 Faz 1 — likidite + manipülasyon kalkanı
        'f_adv_tl': None, 'f_liquidity_tier': None, 'f_manip_risk': None,
        # 6 Haz 2026 — POC backtest-doğrulamalı flag'ler
        'f_poc_magnet': None, 'f_poc_confluence': None, 'f_avwap_test_zone': None,
        # 6 Haz 2026 — Master Score breakdown sub-skorlar (component analizi için)
        'f_ms_trend': None, 'f_ms_momentum': None, 'f_ms_ict': None, 'f_ms_radar2': None,
        # 8 Haz 2026 Oturum 19 — Dual-window genişleme (CMF kalıbı)
        'f_cum_delta_dual': None, 'f_rsi_dual': None,
        # 9 Haz 2026 Oturum 20 — Breakout state (pattern boundary tabanlı, scan_chart_patterns'tan çekilir)
        'f_breakout_state': None,
        # 9 Haz 2026 Oturum 20 son — SMC kurumsal 4 yeni flag (Eylül 2026 backtest)
        'f_at_vwap_minus_2sigma': None,
        'f_at_y_open': None,
        'f_near_ifvg': None,
        'f_breaker_block_active': None,
        # 9 Haz 2026 Oturum 20 — KURUMSAL TAKİP 8 STRONG flag (TEFAS + KAP)
        'f_tefas_konsensus_alim': None,
        'f_tefas_konsensus_satim': None,
        'f_tefas_yeni_giris': None,
        'f_buyback_aktif': None,
        'f_buyback_dip_aliyor': None,
        'f_threshold_asildi': None,
        'f_insider_first_buy': None,
        'f_kurumsal_anchor': None,
        # 10 Haz 2026 Oturum 20 — MFI Dual + RSI/MFI Bouquet
        'f_mfi_dual': None,
        'f_rsi_mfi_bouquet': None,
        # 10 Haz 2026 Oturum 20 — MKK Yabancı Net Alış
        'f_yabanci_giris': None,
        'f_yabanci_cikis': None,
        'f_yabanci_streak': None,
        'f_yabanci_anchor': None,
        # 10 Haz 2026 Oturum 20 — Relative OBV (hisse vs endeks)
        'f_rel_obv_state': None,
        'f_rel_obv_divergence': None,
        # 10 Haz 2026 Oturum 20 — YAPISAL vs TACTICAL skor ayrımı
        'f_smart_structural_score': None,
        'f_smart_tactical_score': None,
        # 10 Haz 2026 Oturum 20 — Up/Down Volume Ratio (Wyckoff)
        'f_udvr_20g': None,
        'f_udvr_state': None,
        'f_udvr_climax': None,
        # 10 Haz 2026 Oturum 20 — Force Index Dual (Elder)
        'f_force_index_dual': None,
        'f_force_index_divergence': None,
        # 12 Haz 2026 — Tek-mum dominance bitmask
        # Bit 0=OBV(5g), 1=CumDelta(5g), 2=CMF(5g), 3=RSI(5), 4=MFI(5).
        # >%60 katkı = bit set. 0 = saf, dual-window "iki periyot teyit" gerçek.
        'f_spike_dominance': 0,
        # 18 Haz 2026 — Risk profili (Beta + DD + HV + Skew)
        'f_beta_xu100': None,
        'f_dd_zirveden': None,
        'f_hv_oran': None,
        'f_skew_60g': None,
        # 29 Haz 2026 — Kurumsal-tarz faktörler (kesitsel; sıralama READ-time panelde)
        'f_mom_12_1': None, 'f_vol_60g': None, 'f_sharpe_mom': None, 'f_trend_persist': None,
        # 17 Tem 2026 EKRAN REFORMU 2c — SFP tuzak flag'leri (PA panelinde gösteriliyordu
        # ama HİÇ loglanmıyordu → Eylül 2026 karnesi "BIST'te tuzak sinyali dönüş öngörüyor mu"
        # sorusunu bununla ölçecek. Tek kaynak: ict_core.compute_sfp_flags.
        'f_sfp_bull': None, 'f_sfp_bear': None,
    }
    try:
        df = get_safe_historical_data(ticker, period="1y")
        if df is None or len(df) < 60:
            return out
        c = df['Close']
        # 1) f_52h_pos — 252g range içindeki konum %
        try:
            seg = df.tail(252)
            h52 = float(seg['High'].max()); l52 = float(seg['Low'].min()); cv = float(c.iloc[-1])
            if h52 > l52:
                out['f_52h_pos'] = round((cv - l52) / (h52 - l52) * 100, 1)
        except Exception: pass
        # 2) f_rsi — Terazi ile aynı tek kaynak
        out['f_rsi'] = _terazi_rsi14_value(df)
        # 3) f_cmf_dual — 5g + 20g state machine (7 state)
        try:
            out['f_cmf_dual'] = _terazi_cmf_dual_state(df)
            # Spike dominance bit 2 — bugünkü money flow vol / 5g toplam money flow vol
            try:
                _rng_cmf = (df['High'] - df['Low']).replace(0, np.nan)
                _mfm = ((df['Close'] - df['Low']) - (df['High'] - df['Close'])) / _rng_cmf
                _mfv = (_mfm * df['Volume']).fillna(0)
                if _spike_dom_ratio(_mfv.iloc[-1], _mfv.tail(5).sum()) > SPIKE_DOM_THRESHOLD:
                    out['f_spike_dominance'] |= (1 << 2)
            except Exception: pass
        except Exception: pass
        # 4) f_omi_sigma — EMA(OBV,5) − EMA(OBV,20), 50-bar std normalize
        try:
            obv_dir = np.sign(c.diff()).fillna(0)
            obv = (df['Volume'] * obv_dir).cumsum()
            omi = obv.ewm(span=5).mean() - obv.ewm(span=20).mean()
            std = omi.rolling(50).std().iloc[-1]
            if std and std > 0:
                out['f_omi_sigma'] = round(float(omi.iloc[-1] / std), 2)
            # Spike dominance bit 0 — bugünkü OBV katkısı / 5g OBV deltası
            try:
                _obv_td = float(obv.iloc[-1] - obv.iloc[-2])
                _obv_5w = float(obv.iloc[-1] - obv.iloc[-6])
                if _spike_dom_ratio(_obv_td, _obv_5w) > SPIKE_DOM_THRESHOLD:
                    out['f_spike_dominance'] |= (1 << 0)
            except Exception: pass
        except Exception: pass
        # 5) f_squeeze_days — BB ⊂ Keltner kaç gündür (trailing count)
        try:
            ma = c.rolling(20).mean(); sd = c.rolling(20).std()
            bb_up, bb_dn = ma + 2*sd, ma - 2*sd
            atr20 = (df['High'] - df['Low']).rolling(20).mean()
            kc_up, kc_dn = ma + 1.5*atr20, ma - 1.5*atr20
            squeezed = (bb_up < kc_up) & (bb_dn > kc_dn)
            cnt = 0
            for v in squeezed.iloc[::-1]:
                if bool(v): cnt += 1
                else: break
            out['f_squeeze_days'] = int(cnt)
        except Exception: pass
        # 6) f_vp_shape — POC'un Value Area içindeki konumu (60g lookback, basit)
        try:
            seg = df.tail(60)
            bins = 30
            prices = ((seg['High'] + seg['Low']) / 2).values
            vols = seg['Volume'].values
            hist, edges = np.histogram(prices, bins=bins, weights=vols)
            poc_idx = int(np.argmax(hist))
            poc_price = (edges[poc_idx] + edges[poc_idx+1]) / 2
            total = hist.sum()
            if total > 0:
                # Value Area = en yüksek hacim seviyesinden başlayıp %70 hacme ulaşan aralık
                order = np.argsort(hist)[::-1]
                cum = 0; included = set()
                for i in order:
                    included.add(int(i)); cum += hist[i]
                    if cum / total >= 0.70: break
                inc_list = sorted(included)
                va_l = edges[inc_list[0]]; va_h = edges[inc_list[-1]+1]
                if va_h > va_l:
                    pos = (poc_price - va_l) / (va_h - va_l)
                    if pos < 0.4:   out['f_vp_shape'] = 'akumulasyon'
                    elif pos > 0.6: out['f_vp_shape'] = 'dagitim'
                    else:           out['f_vp_shape'] = 'denge'
        except Exception: pass
        # 7) f_master_score — calculate_master_score(return_breakdown=True) ile birlikte sub-skorlar
        try:
            _ms_ret = calculate_master_score(ticker, return_breakdown=True)
            if isinstance(_ms_ret, tuple) and len(_ms_ret) >= 4:
                _final, _pros, _cons, _bd = _ms_ret
                out['f_master_score'] = round(float(_final), 1)
                if isinstance(_bd, dict):
                    if 'trend'    in _bd: out['f_ms_trend']    = round(float(_bd['trend'].get('score', 0)), 1)
                    if 'momentum' in _bd: out['f_ms_momentum'] = round(float(_bd['momentum'].get('score', 0)), 1)
                    if 'ict'      in _bd: out['f_ms_ict']      = round(float(_bd['ict'].get('score', 0)), 1)
                    if 'radar2'   in _bd: out['f_ms_radar2']   = round(float(_bd['radar2'].get('score', 0)), 1)
            elif isinstance(_ms_ret, tuple) and len(_ms_ret) >= 1:
                out['f_master_score'] = round(float(_ms_ret[0]), 1)
        except Exception: pass

        # 7b) KÖR SKORLAR (19 Haz 2026 audit) — Sentiment/ICT-model/SmartMoney prominently
        # gösteriliyordu ama hiç loglanmıyordu → backtest edilemiyordu (Master Skor gibi ters
        # olabilirler, bilemiyorduk). Artık loglanıyor; sadece taramaya yakalanan hisseler için
        # (cache'li) hesaplanır → performans sınırlı. Temmuz'da getiriyle test edilecek.
        try:
            _snt = calculate_sentiment_score(ticker)
            if isinstance(_snt, dict) and _snt.get('total') is not None:
                out['f_sentiment_score'] = round(float(_snt['total']), 1)
        except Exception: pass
        try:
            _ictd = calculate_ict_deep_analysis(ticker)
            if isinstance(_ictd, dict) and _ictd.get('model_score') is not None:
                out['f_ict_model'] = round(float(_ictd['model_score']), 1)
        except Exception: pass
        try:
            _smq = calculate_smart_money_score(ticker)
            if isinstance(_smq, dict) and _smq.get('score') is not None:
                out['f_smart_money_score'] = round(float(_smq['score']), 1)
        except Exception: pass

        # 7d) 17 Tem 2026 REFORM 2c — SFP tuzak flag'leri (aynı df, ~0 maliyet)
        try:
            out['f_sfp_bull'], out['f_sfp_bear'] = compute_sfp_flags(df)
        except Exception: pass

        # 7c) Faz 1 (19 Haz 2026) — Likidite + manipülasyon kalkanı
        try:
            _lm = _liquidity_manip(df)
            out['f_adv_tl']         = _lm['adv_mn']
            out['f_liquidity_tier'] = _lm['tier']
            out['f_manip_risk']     = _lm['manip']
        except Exception: pass

        # 11) f_cum_delta_dual — 5g/20g cum_delta state (CMF disiplini, 7 state)
        # cum_delta = (Close-Low)/Range × Vol − (High-Close)/Range × Vol  (calculate_volume_delta proxy)
        # Normalize: cum_n / total_vol_n, eşik ±%5 = strong
        try:
            _rng = (df['High'] - df['Low']).replace(0, np.nan)
            _bp = (df['Close'] - df['Low']) / _rng
            _sp = (df['High'] - df['Close']) / _rng
            _vd = (df['Volume'] * _bp - df['Volume'] * _sp).fillna(0)
            _cum5  = float(_vd.tail(5).sum());  _tot5  = float(df['Volume'].tail(5).sum())
            _cum20 = float(_vd.tail(20).sum()); _tot20 = float(df['Volume'].tail(20).sum())
            _p5  = (_cum5  / _tot5  * 100.0) if _tot5  > 0 else 0
            _p20 = (_cum20 / _tot20 * 100.0) if _tot20 > 0 else 0
            if   _p5 > 5  and _p20 > 5:  out['f_cum_delta_dual'] = 'strong_pos'
            elif _p5 < -5 and _p20 < -5: out['f_cum_delta_dual'] = 'strong_neg'
            elif _p5 > 0  and _p20 < 0:  out['f_cum_delta_dual'] = 'turning_up'    # short toparlanma, ana dağıtım
            elif _p5 < 0  and _p20 > 0:  out['f_cum_delta_dual'] = 'turning_down'  # short profit-taking, ana birikim
            elif _p20 > 5:                out['f_cum_delta_dual'] = 'pos'
            elif _p20 < -5:               out['f_cum_delta_dual'] = 'neg'
            else:                         out['f_cum_delta_dual'] = 'neutral'
            # Spike dominance bit 1 — bugünkü volume delta / 5g toplam
            try:
                if _spike_dom_ratio(_vd.iloc[-1], _cum5) > SPIKE_DOM_THRESHOLD:
                    out['f_spike_dominance'] |= (1 << 1)
            except Exception: pass
        except Exception: pass

        # 12) f_rsi_dual — RSI(5)/RSI(14) state (7 state)
        # RSI(5) erken sinyal — overbought/oversold'a RSI(14)'ten daha hızlı girer
        # cooling_overheat: RSI(5) zaten soğumaya başladı ama RSI(14) hâlâ aşırı alımda → tepe yorgunluğu
        # dip_recovery: RSI(5) toparlanıyor, RSI(14) hâlâ dip → erken dönüş adayı
        try:
            _d = c.diff()
            _g5 = _d.where(_d > 0, 0).rolling(5).mean()
            _l5 = (-_d.where(_d < 0, 0)).rolling(5).mean()
            _rs5 = _g5 / _l5
            _rsi5 = float((100 - (100 / (1 + _rs5))).iloc[-1])
            _rsi14 = out.get('f_rsi')
            if _rsi14 is not None:
                if   _rsi5 >= 80 and _rsi14 >= 70: out['f_rsi_dual'] = 'overbought_both'
                elif _rsi5 <= 20 and _rsi14 <= 30: out['f_rsi_dual'] = 'oversold_both'
                elif _rsi5 >= 80 and _rsi14 < 60:  out['f_rsi_dual'] = 'early_overbought'  # RSI(5) önde — erken uyarı
                elif _rsi5 <= 20 and _rsi14 > 40:  out['f_rsi_dual'] = 'early_oversold'    # RSI(5) önde dip
                elif _rsi5 < 50  and _rsi14 >= 70: out['f_rsi_dual'] = 'cooling_overheat'  # tepe yorgunluğu
                elif _rsi5 > 50  and _rsi14 <= 30: out['f_rsi_dual'] = 'dip_recovery'      # erken dönüş
                else:                              out['f_rsi_dual'] = 'neutral'
                # Spike dominance bit 3 — bugünkü RSI(5) hareketi / 5g toplam hareket
                try:
                    _rsi5_series = (100 - (100 / (1 + _rs5)))
                    _rsi_td = float(_rsi5_series.iloc[-1] - _rsi5_series.iloc[-2])
                    _rsi_5w = float(_rsi5_series.iloc[-1] - _rsi5_series.iloc[-6])
                    if _spike_dom_ratio(_rsi_td, _rsi_5w) > SPIKE_DOM_THRESHOLD:
                        out['f_spike_dominance'] |= (1 << 3)
                except Exception: pass
        except Exception: pass

        # 12.5) f_mfi_dual — MFI(5)/MFI(14) dual-window state (7 state)
        # Wyckoff/VSA'nın hacim-ağırlıklı RSI versiyonu. RSI fiyat momentumu,
        # MFI bunun hacim teyitli halini ölçer. RSI ile aynı kalıp ama "smart money"
        # tonu daha güçlü çünkü hacim filtre var.
        #
        # Eşikler MFI için: ≥80 / ≤20 (RSI'nın 70/30'undan daha sıkı çünkü
        # hacim teyit aradığı için extreme daha nadir, daha güçlü)
        # NOT: Volume güvenilmez sembollerde (endeks/emtia/kripto) None kalır.
        try:
            _vol_unreliable = (
                ticker.upper().startswith(('XU', 'XB', 'XT', 'XY', '^'))
                or ticker.upper().endswith('=F')
                or '-USD' in ticker.upper()
            )
            if not _vol_unreliable and 'Volume' in df.columns:
                _mfi5_series  = compute_mfi(df, period=5)
                _mfi14_series = compute_mfi(df, period=14)
                if len(_mfi5_series) >= 1 and len(_mfi14_series) >= 1:
                    _mfi5  = float(_mfi5_series.iloc[-1])
                    _mfi14 = float(_mfi14_series.iloc[-1])
                    if   _mfi5 >= 80 and _mfi14 >= 75: out['f_mfi_dual'] = 'overbought_both'       # smart money tepede tam dolu
                    elif _mfi5 <= 20 and _mfi14 <= 25: out['f_mfi_dual'] = 'oversold_both'         # smart money dipte tam boş
                    elif _mfi5 >= 80 and _mfi14 < 60:  out['f_mfi_dual'] = 'early_overbought'      # smart money agresif giriyor, fiyat henüz değil
                    elif _mfi5 <= 20 and _mfi14 > 40:  out['f_mfi_dual'] = 'early_oversold'        # ani panik satışı, ana trend henüz değişmedi
                    elif _mfi5 < 50  and _mfi14 >= 75: out['f_mfi_dual'] = 'cooling_smart_exit'    # smart money çıkmaya başladı, fiyat hâlâ tepede
                    elif _mfi5 > 50  and _mfi14 <= 25: out['f_mfi_dual'] = 'smart_money_recovery'  # smart money dipten dönüyor, ana fiyat hâlâ baskı altında
                    else:                              out['f_mfi_dual'] = 'neutral'
                    # Spike dominance bit 4 — bugünkü MFI(5) hareketi / 5g toplam
                    try:
                        _mfi_td = float(_mfi5_series.iloc[-1] - _mfi5_series.iloc[-2])
                        _mfi_5w = float(_mfi5_series.iloc[-1] - _mfi5_series.iloc[-6])
                        if _spike_dom_ratio(_mfi_td, _mfi_5w) > SPIKE_DOM_THRESHOLD:
                            out['f_spike_dominance'] |= (1 << 4)
                    except Exception: pass

                    # f_rsi_mfi_bouquet — ELIT confluence flag
                    # AÇIKLAMA: RSI ve MFI aynı yönde ekstreme + dual-window aynı durumda.
                    # Pratikte iki bağımsız teyit anlamına gelir:
                    #   - Fiyat momentumu (RSI) → "yön zayıflıyor/güçleniyor"
                    #   - Hacim momentumu (MFI) → "akıllı para destek veriyor/çekiliyor"
                    # İkisi de aynı yönde TAM AŞIRI ALIM/SATIM ise = TIER_1 sinyal.
                    # Wyckoff'un "Effort vs Result" eşit ve aşırı = climax noktası
                    # tezi ile birebir uyumlu. Nadir ama güçlü.
                    _rsi_dual = out.get('f_rsi_dual')
                    _mfi_dual = out['f_mfi_dual']
                    _bouquet_states = {'overbought_both', 'oversold_both',
                                       'cooling_overheat', 'cooling_smart_exit',
                                       'dip_recovery', 'smart_money_recovery'}
                    _aligned_pairs = {
                        ('overbought_both', 'overbought_both'),  # ikisi de tepe
                        ('oversold_both', 'oversold_both'),       # ikisi de dip
                        ('cooling_overheat', 'cooling_smart_exit'),  # ikisi de tepe yorgunluğu
                        ('dip_recovery', 'smart_money_recovery'),    # ikisi de dipten dönüş
                    }
                    out['f_rsi_mfi_bouquet'] = 1 if (_rsi_dual, _mfi_dual) in _aligned_pairs else 0
        except Exception: pass

        # 8-10) POC-tabanlı 3 flag (backtest 84.832 event'lik segmente göre)
        try:
            cur = float(c.iloc[-1])
            # f_poc_confluence — 20g/60g/250g POC spread < %2
            try:
                _mtf = calculate_multi_tf_pocs(df, current_price=cur)
                out['f_poc_confluence'] = 1 if (_mtf.get('confluence')) else 0
            except Exception:
                _mtf = None

            # f_avwap_test_zone — 52H zirve veya 52H dip aVWAP fiyata %2 içinde
            try:
                _aw = df.tail(252) if len(df) > 252 else df
                if len(_aw) >= 20:
                    _start = len(df) - len(_aw)
                    _hi_idx = _start + int(np.argmax(_aw['High'].values))
                    _lo_idx = _start + int(np.argmin(_aw['Low'].values))
                    _today_idx = len(df) - 1
                    _in_zone = False
                    for _idx in (_hi_idx, _lo_idx):
                        if _idx >= _today_idx: continue
                        _s = calculate_anchored_vwap(df, _idx)
                        if _s is None or len(_s) < 2: continue
                        _av = float(_s.iloc[-1])
                        if np.isnan(_av) or _av <= 0: continue
                        if abs(cur - _av) / _av * 100.0 < 2.0:
                            _in_zone = True; break
                    out['f_avwap_test_zone'] = 1 if _in_zone else 0
            except Exception: pass

            # f_breakout_state — pattern boundary tabanlı (0/1/2/3) — scan_chart_patterns ChartData
            # Sadece TOBO/Yükselen Üçgen/Range/Çift Dip formasyonları breakout_state set eder.
            # Cache'li (_compute_signal_features kendisi cache'li, scan_chart_patterns get_batch_data_cached).
            try:
                _pat = scan_chart_patterns([ticker])
                if _pat is not None and not _pat.empty:
                    _cd = _pat.iloc[0].get('ChartData', None)
                    if isinstance(_cd, dict) and 'breakout_state' in _cd:
                        out['f_breakout_state'] = int(_cd.get('breakout_state', 0))
            except Exception:
                pass

            # f_poc_magnet — Akümülasyon|Up|below (%67.6) veya Denge|Up|below (%62.0)
            try:
                if _mtf and _mtf.get('poc_20'):
                    _dist20 = _mtf.get('dist_from_price_pct', {}).get('poc_20', 0)
                    if _dist20 < 0 and abs(_dist20) >= 3.0:
                        # Ana trend yukarı: fiyat > SMA50 + 10g eğim > %+1
                        _s50_series = c.rolling(50, min_periods=20).mean()
                        _s50_now = float(_s50_series.iloc[-1])
                        _s50_10g = float(_s50_series.iloc[-11]) if len(_s50_series) >= 11 else _s50_now
                        _slope = ((_s50_now - _s50_10g) / _s50_10g * 100.0) if _s50_10g > 0 else 0
                        _trend_up = (cur > _s50_now) and (_slope > 1.0)
                        _vp_ok = out.get('f_vp_shape') in ('akumulasyon', 'denge')
                        out['f_poc_magnet'] = 1 if (_trend_up and _vp_ok) else 0
                    else:
                        out['f_poc_magnet'] = 0
            except Exception: pass
        except Exception: pass

        # ─── 13-16) SMC KURUMSAL 4 FLAG (9 Haz 2026 Oturum 20 son) ──────────
        # Bunlar henüz BIST için backtest edilmedi — Eylül 2026 ortası
        # signal_returns × bu flag'ler JOIN ile gerçek hit/ret katkısı ölçülecek.
        # AI prompt'a şu an "destekleyici" seviyede emit edilir (ana hikaye değil).
        try:
            cur = float(c.iloc[-1])

            # 13) f_at_vwap_minus_2sigma — fiyat -2σ VWAP zonunda (±%0.5 içinde)
            try:
                _vwap_full = (df['Close'] * df['Volume']).cumsum() / df['Volume'].cumsum()
                _close_arr_n = df['Close'].values.astype(float)
                _vwap_arr_n = _vwap_full.values.astype(float)
                _dev_s = pd.Series(_close_arr_n - _vwap_arr_n)
                _sigma_now = float(_dev_s.rolling(20, min_periods=5).std().iloc[-1])
                if _sigma_now > 0:
                    _lower_2_now = float(_vwap_arr_n[-1]) - 2 * _sigma_now
                    if _lower_2_now > 0 and abs(cur - _lower_2_now) / _lower_2_now * 100.0 <= 0.5:
                        out['f_at_vwap_minus_2sigma'] = 1
                    else:
                        out['f_at_vwap_minus_2sigma'] = 0
            except Exception: pass

            # 14) f_at_y_open — fiyat Y-Open'a ±%2 içinde
            try:
                _last_dt = df.index[-1]
                _yr = _last_dt.year
                _y_bars = df[df.index.year == _yr]
                if len(_y_bars) >= 1:
                    _y_open_val = float(_y_bars['Open'].iloc[0])
                    if _y_open_val > 0 and abs(cur - _y_open_val) / _y_open_val * 100.0 <= 2.0:
                        out['f_at_y_open'] = 1
                    else:
                        out['f_at_y_open'] = 0
            except Exception: pass

            # 15-16) f_near_ifvg + f_breaker_block_active — SMC structure'dan
            try:
                _highs = df['High'].values.astype(float)
                _lows  = df['Low'].values.astype(float)
                _opens = df['Open'].values.astype(float)
                _closes= df['Close'].values.astype(float)
                _smc_n = _compute_smc_elements(_highs, _lows, _opens, _closes)
                # 15) f_near_ifvg — inverted FVG'ye ±%2 içinde mi
                _near_iv = 0
                for _fv in (_smc_n.get('fvg_bull', []) + _smc_n.get('fvg_bear', [])):
                    if len(_fv) >= 4 and _fv[3] == 'inverted':
                        _mid = (_fv[1] + _fv[2]) / 2.0
                        if _mid > 0 and abs(cur - _mid) / _mid * 100.0 <= 2.0:
                            _near_iv = 1; break
                out['f_near_ifvg'] = _near_iv
                # 16) f_breaker_block_active — aktif BB zonunda ±%2 içinde mi
                _near_bb = 0
                for _ob in (_smc_n.get('ob_bull', []) + _smc_n.get('ob_bear', [])):
                    if len(_ob) >= 6 and _ob[5] == 'breaker':
                        _mid = (_ob[3] + _ob[4]) / 2.0  # body_hi + body_lo
                        if _mid > 0 and abs(cur - _mid) / _mid * 100.0 <= 2.0:
                            _near_bb = 1; break
                out['f_breaker_block_active'] = _near_bb
            except Exception: pass
        except Exception: pass

        # ─── MKK Yabancı + Relative OBV + UDVR + Force Index ────────────────
        # (12 Haz Oturum 21) TEFAS + KAP KALDIRILDI: TEFAS yeni API hisse-bazlı veri
        # vermiyor (sadece makro fon allocation = kalıcı rejim yasağı kapsamında),
        # KAP endpoint bloklu (status 666). f_tefas_*/f_buyback_*/f_threshold_*/
        # f_insider_*/f_kurumsal_anchor kolonları NULL kalır (backtest schema korunur).
        try:
            # MKK Yabancı Net Alış (İş Yatırım RSS) — KORUNDU (hisse-bazlı, çalışıyor)
            _mkk_y = _compute_mkk_yabanci_signals(ticker)
            for _k in ('f_yabanci_giris', 'f_yabanci_cikis', 'f_yabanci_streak', 'f_yabanci_anchor'):
                if _mkk_y.get(_k) is not None:
                    out[_k] = _mkk_y[_k]
            # Risk profili (18 Haz 2026) — Beta + Drawdown + HV + Skew
            try:
                _rp_feat = _compute_risk_profile(ticker)
                if _rp_feat.get('beta_xu100') is not None:
                    out['f_beta_xu100'] = _rp_feat['beta_xu100']
                if _rp_feat.get('dd_zirveden') is not None:
                    out['f_dd_zirveden'] = _rp_feat['dd_zirveden']
                if _rp_feat.get('hv_oran') is not None:
                    out['f_hv_oran'] = _rp_feat['hv_oran']
                if _rp_feat.get('skew_60g') is not None:
                    out['f_skew_60g'] = _rp_feat['skew_60g']
            except Exception: pass
            # Relative OBV (hisse vs endeks) — hacim akışı ayrışması
            # Volume güvenilmez sembollerde atla; benchmark fetch ile çakışıyor olabilir
            try:
                _vol_no_rel = (
                    ticker.upper().startswith(('XU', 'XB', 'XT', 'XY', '^'))
                    or ticker.upper().endswith('=F')
                    or '-USD' in ticker.upper()
                )
                if not _vol_no_rel and df is not None and len(df) >= 25:
                    _bench_t_rel = "XU100.IS" if ".IS" in ticker else "^GSPC"
                    _df_bench_rel = get_safe_historical_data(_bench_t_rel, period="3mo")
                    if _df_bench_rel is not None and len(_df_bench_rel) >= 25:
                        _rel_res = compute_relative_obv_state(df, _df_bench_rel, lookback=20)
                        if _rel_res:
                            out['f_rel_obv_state'] = _rel_res['state']
                            out['f_rel_obv_divergence'] = (
                                1 if _rel_res['state'] in ('outperform_strong', 'underperform_strong') else 0
                            )
                # Up/Down Volume Ratio (Wyckoff Effort-vs-Result, 20g pencere)
                if not _vol_no_rel and df is not None and len(df) >= 21:
                    _udvr_r = compute_updown_volume_ratio(df, period=20)
                    if _udvr_r:
                        out['f_udvr_20g'] = _udvr_r.get('ratio')
                        out['f_udvr_state'] = _udvr_r.get('state')
                        out['f_udvr_climax'] = _udvr_r.get('climax')
                # Force Index Dual (Elder Triple Screen)
                if not _vol_no_rel and df is not None and len(df) >= 50:
                    _fi_r = compute_force_index_dual(df, span_short=2, span_long=13)
                    if _fi_r:
                        out['f_force_index_dual'] = _fi_r.get('state')
                        out['f_force_index_divergence'] = _fi_r.get('divergence')
            except Exception: pass
            # YAPISAL vs TACTICAL skor ayrımı (tüm flag'ler hesaplandıktan sonra)
            _split = compute_smart_money_split_scores(out)
            if _split:
                out['f_smart_structural_score'] = _split.get('structural_score')
                out['f_smart_tactical_score'] = _split.get('tactical_score')
        except Exception: pass

        # ─── KURUMSAL-TARZ FAKTÖRLER (29 Haz 2026) ──────────────────────────
        # GS/JPM tarzı: gösterge değil faktör. Ham değerleri burada loglanır,
        # KESİTSEL SIRALAMA (quintile) okuma anında "Kriter Performansı" panelinde
        # cohort içinde yapılır. Hepsi fiyat-only → ek veri/maliyet yok.
        try:
            _cf = c.astype(float)
            _ret_d = _cf.pct_change().dropna()
            # 1) Momentum 12-1 ay: 252g önce → 21g önce (son ay atlanır, kurumsal standart)
            if len(_cf) >= 252:
                _p_then = float(_cf.iloc[-252]); _p_skip = float(_cf.iloc[-21])
                if _p_then > 0:
                    out['f_mom_12_1'] = round((_p_skip / _p_then - 1.0) * 100.0, 2)
            # 2) Düşük-vol faktörü: 60g günlük getiri std, yıllıklandırılmış %
            if len(_ret_d) >= 60:
                out['f_vol_60g'] = round(float(_ret_d.tail(60).std()) * (252 ** 0.5) * 100.0, 2)
            # 3) Riske-göre momentum: 120g ortalama/std, yıllık Sharpe-benzeri
            if len(_ret_d) >= 120:
                _seg = _ret_d.tail(120); _sd = float(_seg.std())
                if _sd > 0:
                    out['f_sharpe_mom'] = round(float(_seg.mean()) / _sd * (252 ** 0.5), 2)
            # 4) Trend kalıcılığı: son 120g'de kapanışın SMA50 üstünde kalma %
            if len(_cf) >= 80:
                _sma50 = _cf.rolling(50).mean()
                _above = (_cf > _sma50).dropna().tail(120)
                if len(_above) >= 20:
                    out['f_trend_persist'] = round(float(_above.mean()) * 100.0, 1)
        except Exception: pass

    except Exception as e:
        try: log_error("compute_signal_features", e, ctx={'ticker': ticker})
        except Exception: pass
    return out


# ── BAYAT VERİ YAZIM KAPISI (19 Ağu 2026) ────────────────────────────────────
# 18 Ağu: lokal ayna 13:55'te donmuşken tarama koştu ve öğlen fiyatlarıyla 1.437
# sinyal yazdı (giriş fiyatı ort. %1,8 — en kötüsü %15 sapma). Kayıtlar silindi.
# Artık sinyal yazımı, deponun beklenen seansı taşımasına bağlı: bayatsa HİÇ
# yazılmaz (yarım/kirli gün oluşmaz). Ölçüt tek kaynakta: depo_tazelik.yazim_izni.
# Zorunlu hâlde kapıyı aç: SMR_BAYAT_YAZIM_IZNI=1
_BAYAT_UYARI_VERILDI = False


def _bayat_yazim_kapisi() -> str:
    """Yazıma engel varsa gerekçeyi döner; sorun yoksa boş string."""
    if os.environ.get("SMR_BAYAT_YAZIM_IZNI") == "1":
        return ""
    try:
        from depo_tazelik import yazim_izni
        ok, sebep = yazim_izni()
    except Exception:
        # Kapının kendi arızası taramayı durdurmasın — eski davranışa düş.
        return ""
    return "" if ok else sebep


def _bayat_uyar(sebep: str) -> None:
    """Ekrana + log'a bir kez yaz (her tarayıcı için tekrar etmesin)."""
    global _BAYAT_UYARI_VERILDI
    if _BAYAT_UYARI_VERILDI:
        return
    _BAYAT_UYARI_VERILDI = True
    mesaj = ("🔴 VERİ BAYAT — tarama sonuçları kaydedilmedi. %s. "
             "Depo tazelenince taramayı tekrar çalıştır." % sebep)
    logging.warning("[log_scan_signal] %s" % mesaj)
    try:
        st.error(mesaj)
    except Exception:
        pass


def log_scan_signal(scan_type: str, df_result, category: str = "", _lock_retry: int = 0):
    """
    Scan sonuçlarını signals.db'ye (patron.db içinde scan_signals tablosuna) yazar.
    Aynı gün aynı scan_type + symbol kombinasyonu varsa INSERT OR IGNORE ile atlar.
    9 Tem 2026: 'database is locked' gelirse bekle + 3 kez yeniden dene (8 Tem kazası:
    19:30 gece backtest'i kilidi tutarken rs_leaders'ın ~80 kaydı sessizce yutulmuştu).
    """
    if _SCAN_LOG_DISABLED:
        return True   # tek-hisse canlı tarama → DB log'u atla
    _bayat = _bayat_yazim_kapisi()
    if _bayat:
        _bayat_uyar(_bayat)
        return False
    today = datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d")
    if _SCAN_LOG_SKIP and df_result is not None and hasattr(df_result, 'columns'):
        # sadece atlama setindeki ticker satırlarını süz — diğer semboller loglanır
        try:
            for _c in ('Sembol', 'Hisse', 'Ticker', 'Symbol'):
                if _c in df_result.columns:
                    _u = df_result[_c].astype(str).str.upper().str.replace('.IS', '', regex=False)
                    df_result = df_result[~_u.isin(_SCAN_LOG_SKIP)]
                    break
        except Exception:
            return
    if df_result is None or (hasattr(df_result, 'empty') and df_result.empty):
        try:
            with database_write_lock(f"scan_log_{scan_type}"):
                conn = sqlite3.connect(DB_FILE, timeout=60)
                try:
                    previous_run = register_scan_run(conn, scan_type, today, 0, category)
                    assign_event_metadata_for_date(conn, scan_type, today, previous_run)
                    conn.commit()
                finally:
                    conn.close()
            return True
        except Exception as e:
            logging.warning(f"[log_scan_signal] HATA — scan_type={scan_type}: {e}")
            return False
    # B4-2: df'te feature kolonu yoksa toplu hesapla (cache'li, ticker başı tek hesap)
    _has_feat_cols = any(str(_col).lower().startswith('f_') or str(_col).startswith('F_')
                         for _col in df_result.columns)
    _feat_cache = {}
    if not _has_feat_cols:
        try:
            # 20 Haz 2026 — sembol kolonu alias-tolerant (Altın/Platin 'Hisse' kullanıyor, 'Sembol' değil)
            _sym_col = next((cc for cc in ('Sembol', 'Hisse', 'Ticker', 'symbol') if cc in df_result.columns), None)
            if _sym_col:
                for _sym in df_result[_sym_col].dropna().unique():
                    if _sym and str(_sym) not in _feat_cache:
                        _feat_cache[str(_sym)] = _compute_signal_features(str(_sym))
        except Exception:
            pass
    conn = None
    _write_guard = None
    try:
        _write_guard = database_write_lock(f"scan_log_{scan_type}")
        _write_guard.__enter__()
        conn = sqlite3.connect(DB_FILE, timeout=60)
        ensure_event_schema(conn)
        ensure_deepening_schema(conn)
        previous_run = register_scan_run(conn, scan_type, today, len(df_result), category)
        c = conn.cursor()
        for _, row in df_result.iterrows():
            symbol = row.get('Sembol', '') or row.get('Hisse', '') or row.get('Ticker', '') or row.get('symbol', '')
            if not symbol:
                continue
            entry_raw   = row.get('Fiyat', row.get('fiyat', None))
            score_raw   = row.get('ToplamSkor', row.get('Raw_Score', row.get('Skor', row.get('score', row.get('Teknik_Skor', None)))))
            stop_raw    = row.get('Stop', row.get('stop_level', row.get('StopSeviye', None)))
            try:
                entry_price = float(str(entry_raw).replace(',', '.')) if entry_raw is not None else None
            except Exception:
                entry_price = None
            try:
                score = float(score_raw) if score_raw is not None else None
            except Exception:
                score = None
            try:
                stop_level = float(str(stop_raw).replace(',', '.')) if stop_raw is not None else None
            except Exception:
                stop_level = None
            # B4-1: scanner df'inde OBV_Status varsa yaz, yoksa NULL — geriye uyumlu
            obv_status_raw = row.get('OBV_Status', row.get('obv_status', None))
            obv_status = str(obv_status_raw) if obv_status_raw is not None else None
            # B4-2 (3 Haz 2026): Feature snapshot — scanner df'i bu kolonları içerirse yaz.
            # Çoklu alias ile geriye uyumlu (eski scanner'lar 'F_52H_Pos'/'52H_Pos'/'f_52h_pos' yazabilir).
            def _ff(*keys, cast=float):
                for k in keys:
                    v = row.get(k)
                    if v is not None and v != '':
                        try:
                            return cast(str(v).replace(',', '.')) if cast is float else cast(v)
                        except Exception:
                            return None
                return None
            f_52h_pos      = _ff('F_52H_Pos', '52H_Pos', 'f_52h_pos', 'YillikKonum')
            f_rsi          = _ff('F_RSI', 'RSI', 'f_rsi', 'rsi_14')
            f_cmf_dual_raw = _ff('F_CMF_Dual', 'CMF_Dual', 'f_cmf_dual', 'cmf_state', cast=str)
            f_cmf_dual     = f_cmf_dual_raw if f_cmf_dual_raw else None
            f_omi_sigma    = _ff('F_OMI', 'OMI_Sigma', 'f_omi_sigma', 'omi')
            f_squeeze_days_v = _ff('F_Squeeze_Days', 'Squeeze_Days', 'f_squeeze_days', 'sikisma_gun', cast=int)
            f_vp_shape_raw = _ff('F_VP_Shape', 'VP_Shape', 'f_vp_shape', 'vp_sekil', cast=str)
            f_vp_shape     = f_vp_shape_raw if f_vp_shape_raw else None
            f_master_score = _ff('F_Master', 'MasterScore', 'f_master_score', 'master_score')
            f_poc_magnet_v     = _ff('F_POC_Magnet', 'POC_Magnet', 'f_poc_magnet', cast=int)
            f_poc_confluence_v = _ff('F_POC_Confluence', 'POC_Confluence', 'f_poc_confluence', cast=int)
            f_avwap_test_v     = _ff('F_aVWAP_Test', 'aVWAP_Test_Zone', 'f_avwap_test_zone', cast=int)
            f_ms_trend_v       = _ff('F_MS_Trend', 'MS_Trend', 'f_ms_trend')
            f_ms_momentum_v    = _ff('F_MS_Momentum', 'MS_Momentum', 'f_ms_momentum')
            f_ms_ict_v         = _ff('F_MS_ICT', 'MS_ICT', 'f_ms_ict')
            f_ms_radar2_v      = _ff('F_MS_Radar2', 'MS_Radar2', 'f_ms_radar2')
            # 8 Haz 2026 Oturum 19 — Dual-window genişleme
            f_cum_delta_dual_raw = _ff('F_CumDelta_Dual', 'CumDelta_Dual', 'f_cum_delta_dual', cast=str)
            f_cum_delta_dual     = f_cum_delta_dual_raw if f_cum_delta_dual_raw else None
            f_rsi_dual_raw       = _ff('F_RSI_Dual', 'RSI_Dual', 'f_rsi_dual', cast=str)
            f_rsi_dual           = f_rsi_dual_raw if f_rsi_dual_raw else None
            # 9 Haz 2026 Oturum 20 — Breakout state (Kibar Type 1)
            f_breakout_state_v   = _ff('F_Breakout_State', 'Breakout_State', 'f_breakout_state', cast=int)
            # 9 Haz 2026 Oturum 20 son — SMC kurumsal 4 yeni flag
            f_at_vwap_m2s_v      = _ff('F_VWAP_M2S', 'At_VWAP_M2S', 'f_at_vwap_minus_2sigma', cast=int)
            f_at_y_open_v        = _ff('F_Y_Open', 'At_Y_Open', 'f_at_y_open', cast=int)
            f_near_ifvg_v        = _ff('F_iFVG', 'Near_iFVG', 'f_near_ifvg', cast=int)
            f_bb_active_v        = _ff('F_BB_Active', 'Breaker_Block', 'f_breaker_block_active', cast=int)
            # 9 Haz 2026 Oturum 20 — KURUMSAL TAKİP 8 STRONG flag (TEFAS + KAP)
            f_tefas_alim_v       = _ff('F_TEFAS_Alim', 'f_tefas_konsensus_alim', cast=int)
            f_tefas_satim_v      = _ff('F_TEFAS_Satim', 'f_tefas_konsensus_satim', cast=int)
            f_tefas_yeni_v       = _ff('F_TEFAS_Yeni', 'f_tefas_yeni_giris', cast=int)
            f_buyback_aktif_v    = _ff('F_Buyback_Aktif', 'f_buyback_aktif', cast=int)
            f_buyback_dip_v      = _ff('F_Buyback_Dip', 'f_buyback_dip_aliyor', cast=int)
            f_thresh_v           = _ff('F_Threshold', 'f_threshold_asildi', cast=int)
            f_insider_first_v    = _ff('F_Insider_First', 'f_insider_first_buy', cast=int)
            f_anchor_v           = _ff('F_Kurumsal_Anchor', 'f_kurumsal_anchor', cast=int)
            # 10 Haz 2026 — MFI dual + RSI/MFI Bouquet
            f_mfi_dual_raw       = _ff('F_MFI_Dual', 'MFI_Dual', 'f_mfi_dual', cast=str)
            f_mfi_dual           = f_mfi_dual_raw if f_mfi_dual_raw else None
            f_rsi_mfi_bouquet_v  = _ff('F_RSI_MFI_Bouquet', 'f_rsi_mfi_bouquet', cast=int)
            # 10 Haz 2026 — MKK Yabancı
            f_yab_giris_v        = _ff('F_Yab_Giris', 'f_yabanci_giris', cast=int)
            f_yab_cikis_v        = _ff('F_Yab_Cikis', 'f_yabanci_cikis', cast=int)
            f_yab_streak_v       = _ff('F_Yab_Streak', 'f_yabanci_streak', cast=int)
            f_yab_anchor_v       = _ff('F_Yab_Anchor', 'f_yabanci_anchor', cast=int)
            # 10 Haz 2026 — Relative OBV (hisse vs endeks)
            f_rel_obv_raw        = _ff('F_Rel_OBV_State', 'f_rel_obv_state', cast=str)
            f_rel_obv_state      = f_rel_obv_raw if f_rel_obv_raw else None
            f_rel_obv_div_v      = _ff('F_Rel_OBV_Div', 'f_rel_obv_divergence', cast=int)
            # 10 Haz 2026 — YAPISAL vs TACTICAL skor
            f_smart_struct_v     = _ff('F_Smart_Struct', 'f_smart_structural_score')
            f_smart_tact_v       = _ff('F_Smart_Tact', 'f_smart_tactical_score')
            # 10 Haz 2026 — Up/Down Volume Ratio (Wyckoff)
            f_udvr_v             = _ff('F_UDVR_20g', 'f_udvr_20g')
            f_udvr_state_raw     = _ff('F_UDVR_State', 'f_udvr_state', cast=str)
            f_udvr_state         = f_udvr_state_raw if f_udvr_state_raw else None
            f_udvr_climax_raw    = _ff('F_UDVR_Climax', 'f_udvr_climax', cast=str)
            f_udvr_climax        = f_udvr_climax_raw if f_udvr_climax_raw else None
            # 10 Haz 2026 — Force Index Dual (Elder)
            f_fi_dual_raw        = _ff('F_FI_Dual', 'f_force_index_dual', cast=str)
            f_fi_dual            = f_fi_dual_raw if f_fi_dual_raw else None
            f_fi_div_raw         = _ff('F_FI_Div', 'f_force_index_divergence', cast=str)
            f_fi_div             = f_fi_div_raw if f_fi_div_raw else None
            # 12 Haz 2026 — Spike Dominance bitmask
            f_spike_dom_v        = _ff('F_Spike_Dominance', 'f_spike_dominance')
            # 18 Haz 2026 — Tavan motoru (60g backtest, skor ≥150 hit %11.24)
            f_tavan_skor_v       = _ff('F_Tavan_Skor', 'f_tavan_skor', 'tavan_skor')
            f_tavan_kat_raw      = _ff('F_Tavan_Kat', 'f_tavan_kat', 'tavan_kat', cast=str)
            f_tavan_kat          = f_tavan_kat_raw if f_tavan_kat_raw else None
            f_tavan_conf_v       = _ff('F_Tavan_Confluence', 'f_tavan_confluence_n', cast=int)
            # 18 Haz 2026 — Risk profili (Beta + DD + HV + Skew)
            f_beta_xu100_v       = _ff('F_Beta_XU100', 'f_beta_xu100', 'beta_xu100')
            f_dd_zirveden_v      = _ff('F_DD_Zirveden', 'f_dd_zirveden', 'dd_zirveden')
            f_hv_oran_v          = _ff('F_HV_Oran', 'f_hv_oran', 'hv_oran')
            f_skew_60g_v         = _ff('F_Skew_60g', 'f_skew_60g', 'skew_60g')
            # 29 Haz 2026 — Kurumsal-tarz faktörler (scanner df üretmez → sadece _feat'ten gelir)
            f_mom_12_1_v = f_vol_60g_v = f_sharpe_mom_v = f_trend_persist_v = None
            f_sentiment_v = f_ict_model_v = f_smart_money_v = None  # 19 Haz audit — kör skorlar (sadece _feat'ten)
            f_adv_tl_v = f_liq_tier_v = f_manip_v = None            # 19 Haz Faz 1 — likidite/manip (sadece _feat'ten)
            f_sfp_bull_v = f_sfp_bear_v = None                      # 17 Tem reform 2c — SFP tuzak (sadece _feat'ten)
            # B4-2 FALLBACK: scanner df feature üretmiyorsa _feat_cache'ten al
            _feat = _feat_cache.get(str(symbol), {}) if _feat_cache else {}
            if _feat:
                if f_52h_pos       is None: f_52h_pos       = _feat.get('f_52h_pos')
                if f_rsi           is None: f_rsi           = _feat.get('f_rsi')
                if f_cmf_dual      is None: f_cmf_dual      = _feat.get('f_cmf_dual')
                if f_omi_sigma     is None: f_omi_sigma     = _feat.get('f_omi_sigma')
                if f_squeeze_days_v is None: f_squeeze_days_v = _feat.get('f_squeeze_days')
                if f_vp_shape      is None: f_vp_shape      = _feat.get('f_vp_shape')
                if f_master_score  is None: f_master_score  = _feat.get('f_master_score')
                if f_poc_magnet_v     is None: f_poc_magnet_v     = _feat.get('f_poc_magnet')
                if f_poc_confluence_v is None: f_poc_confluence_v = _feat.get('f_poc_confluence')
                if f_avwap_test_v     is None: f_avwap_test_v     = _feat.get('f_avwap_test_zone')
                if f_ms_trend_v       is None: f_ms_trend_v       = _feat.get('f_ms_trend')
                if f_ms_momentum_v    is None: f_ms_momentum_v    = _feat.get('f_ms_momentum')
                if f_ms_ict_v         is None: f_ms_ict_v         = _feat.get('f_ms_ict')
                if f_ms_radar2_v      is None: f_ms_radar2_v      = _feat.get('f_ms_radar2')
                if f_cum_delta_dual   is None: f_cum_delta_dual   = _feat.get('f_cum_delta_dual')
                if f_rsi_dual         is None: f_rsi_dual         = _feat.get('f_rsi_dual')
                if f_breakout_state_v is None: f_breakout_state_v = _feat.get('f_breakout_state')
                if f_at_vwap_m2s_v    is None: f_at_vwap_m2s_v    = _feat.get('f_at_vwap_minus_2sigma')
                if f_at_y_open_v      is None: f_at_y_open_v      = _feat.get('f_at_y_open')
                if f_near_ifvg_v      is None: f_near_ifvg_v      = _feat.get('f_near_ifvg')
                if f_bb_active_v      is None: f_bb_active_v      = _feat.get('f_breaker_block_active')
                if f_tefas_alim_v     is None: f_tefas_alim_v     = _feat.get('f_tefas_konsensus_alim')
                if f_tefas_satim_v    is None: f_tefas_satim_v    = _feat.get('f_tefas_konsensus_satim')
                if f_tefas_yeni_v     is None: f_tefas_yeni_v     = _feat.get('f_tefas_yeni_giris')
                if f_buyback_aktif_v  is None: f_buyback_aktif_v  = _feat.get('f_buyback_aktif')
                if f_buyback_dip_v    is None: f_buyback_dip_v    = _feat.get('f_buyback_dip_aliyor')
                if f_thresh_v         is None: f_thresh_v         = _feat.get('f_threshold_asildi')
                if f_insider_first_v  is None: f_insider_first_v  = _feat.get('f_insider_first_buy')
                if f_anchor_v         is None: f_anchor_v         = _feat.get('f_kurumsal_anchor')
                if f_mfi_dual         is None: f_mfi_dual         = _feat.get('f_mfi_dual')
                if f_rsi_mfi_bouquet_v is None: f_rsi_mfi_bouquet_v = _feat.get('f_rsi_mfi_bouquet')
                if f_yab_giris_v      is None: f_yab_giris_v      = _feat.get('f_yabanci_giris')
                if f_yab_cikis_v      is None: f_yab_cikis_v      = _feat.get('f_yabanci_cikis')
                if f_yab_streak_v     is None: f_yab_streak_v     = _feat.get('f_yabanci_streak')
                if f_yab_anchor_v     is None: f_yab_anchor_v     = _feat.get('f_yabanci_anchor')
                if f_rel_obv_state    is None: f_rel_obv_state    = _feat.get('f_rel_obv_state')
                if f_rel_obv_div_v    is None: f_rel_obv_div_v    = _feat.get('f_rel_obv_divergence')
                if f_smart_struct_v   is None: f_smart_struct_v   = _feat.get('f_smart_structural_score')
                if f_smart_tact_v     is None: f_smart_tact_v     = _feat.get('f_smart_tactical_score')
                if f_udvr_v           is None: f_udvr_v           = _feat.get('f_udvr_20g')
                if f_udvr_state       is None: f_udvr_state       = _feat.get('f_udvr_state')
                if f_udvr_climax      is None: f_udvr_climax      = _feat.get('f_udvr_climax')
                if f_fi_dual          is None: f_fi_dual          = _feat.get('f_force_index_dual')
                if f_fi_div           is None: f_fi_div           = _feat.get('f_force_index_divergence')
                if f_spike_dom_v      is None: f_spike_dom_v      = _feat.get('f_spike_dominance')
                if f_beta_xu100_v     is None: f_beta_xu100_v     = _feat.get('f_beta_xu100')
                if f_dd_zirveden_v    is None: f_dd_zirveden_v    = _feat.get('f_dd_zirveden')
                if f_hv_oran_v        is None: f_hv_oran_v        = _feat.get('f_hv_oran')
                if f_skew_60g_v       is None: f_skew_60g_v       = _feat.get('f_skew_60g')
                if f_mom_12_1_v       is None: f_mom_12_1_v       = _feat.get('f_mom_12_1')
                if f_vol_60g_v        is None: f_vol_60g_v        = _feat.get('f_vol_60g')
                if f_sharpe_mom_v     is None: f_sharpe_mom_v     = _feat.get('f_sharpe_mom')
                if f_trend_persist_v  is None: f_trend_persist_v  = _feat.get('f_trend_persist')
                if f_sentiment_v   is None: f_sentiment_v   = _feat.get('f_sentiment_score')
                if f_ict_model_v   is None: f_ict_model_v   = _feat.get('f_ict_model')
                if f_smart_money_v is None: f_smart_money_v = _feat.get('f_smart_money_score')
                if f_adv_tl_v      is None: f_adv_tl_v      = _feat.get('f_adv_tl')
                if f_liq_tier_v    is None: f_liq_tier_v    = _feat.get('f_liquidity_tier')
                if f_manip_v       is None: f_manip_v       = _feat.get('f_manip_risk')
                if f_sfp_bull_v    is None: f_sfp_bull_v    = _feat.get('f_sfp_bull')
                if f_sfp_bear_v    is None: f_sfp_bear_v    = _feat.get('f_sfp_bear')
            symbol_db = str(symbol).replace('.IS', '')  # 19 Haz audit — .IS tutarlılığı (er ile aynı biçim)
            # 29 Tem 2026 — seçili derinleştirme paketi. Ana tarama formüllerini
            # değiştirmez; kalite, geç kalma ve olay aşamasını aynı sinyal satırına bağlar.
            _quality_score_v = _ff('Kalite_Skoru', 'quality_score')
            _quality_label_v = _ff('Kalite', 'quality_label', cast=str)
            _quality_detail_v = _ff('Kalite_Detay', 'quality_detail', cast=str)
            _journey_stage_v = _ff(
                'Yolculuk_Asamasi', 'Liderlik_Asamasi', 'journey_stage', cast=str
            )
            _journey_age_v = _ff(
                'Yolculuk_Gunu', 'Liderlik_Yasi', 'journey_age', cast=int
            )
            _journey_key_v = _ff(
                'Yolculuk_Anahtari', 'journey_key', cast=str
            )
            # 20 Tem 2026: df 'bias' kolonu taşıyorsa oku (birleşik dtri=bearish). Yoksa eskisi gibi
            # 'bullish' (geriye uyumlu — mevcut çağıranlar bias kolonu geçirmez, davranış değişmez).
            _row_bias = str(row.get('bias', row.get('Bias', 'bullish')) or 'bullish')
            c.execute(
                '''INSERT OR IGNORE INTO scan_signals
                   (scan_date, symbol, scan_type, score, bias, entry_price, stop_level, category, obv_status,
                    f_52h_pos, f_rsi, f_cmf_dual, f_omi_sigma, f_squeeze_days, f_vp_shape, f_master_score,
                    f_poc_magnet, f_poc_confluence, f_avwap_test_zone,
                    f_ms_trend, f_ms_momentum, f_ms_ict, f_ms_radar2,
                    f_cum_delta_dual, f_rsi_dual, f_breakout_state,
                    f_at_vwap_minus_2sigma, f_at_y_open, f_near_ifvg, f_breaker_block_active,
                    f_tefas_konsensus_alim, f_tefas_konsensus_satim, f_tefas_yeni_giris,
                    f_buyback_aktif, f_buyback_dip_aliyor,
                    f_threshold_asildi, f_insider_first_buy, f_kurumsal_anchor,
                    f_mfi_dual, f_rsi_mfi_bouquet,
                    f_yabanci_giris, f_yabanci_cikis, f_yabanci_streak, f_yabanci_anchor,
                    f_rel_obv_state, f_rel_obv_divergence,
                    f_smart_structural_score, f_smart_tactical_score,
                    f_udvr_20g, f_udvr_state, f_udvr_climax,
                    f_force_index_dual, f_force_index_divergence,
                    f_spike_dominance,
                    f_tavan_skor, f_tavan_kat, f_tavan_confluence_n,
                    f_beta_xu100, f_dd_zirveden, f_hv_oran, f_skew_60g,
                    f_sentiment_score, f_ict_model, f_smart_money_score,
                    f_adv_tl, f_liquidity_tier, f_manip_risk,
                    f_mom_12_1, f_vol_60g, f_sharpe_mom, f_trend_persist,
                    f_sfp_bull, f_sfp_bear)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (today, symbol_db, scan_type, score, _row_bias, entry_price, stop_level, category, obv_status,
                 f_52h_pos, f_rsi, f_cmf_dual, f_omi_sigma, f_squeeze_days_v, f_vp_shape, f_master_score,
                 f_poc_magnet_v, f_poc_confluence_v, f_avwap_test_v,
                 f_ms_trend_v, f_ms_momentum_v, f_ms_ict_v, f_ms_radar2_v,
                 f_cum_delta_dual, f_rsi_dual, f_breakout_state_v,
                 f_at_vwap_m2s_v, f_at_y_open_v, f_near_ifvg_v, f_bb_active_v,
                 f_tefas_alim_v, f_tefas_satim_v, f_tefas_yeni_v,
                 f_buyback_aktif_v, f_buyback_dip_v,
                 f_thresh_v, f_insider_first_v, f_anchor_v,
                 f_mfi_dual, f_rsi_mfi_bouquet_v,
                 f_yab_giris_v, f_yab_cikis_v, f_yab_streak_v, f_yab_anchor_v,
                 f_rel_obv_state, f_rel_obv_div_v,
                 f_smart_struct_v, f_smart_tact_v,
                 f_udvr_v, f_udvr_state, f_udvr_climax,
                 f_fi_dual, f_fi_div,
                 f_spike_dom_v,
                 f_tavan_skor_v, f_tavan_kat, f_tavan_conf_v,
                 f_beta_xu100_v, f_dd_zirveden_v, f_hv_oran_v, f_skew_60g_v,
                 f_sentiment_v, f_ict_model_v, f_smart_money_v,
                 f_adv_tl_v, f_liq_tier_v, f_manip_v,
                 f_mom_12_1_v, f_vol_60g_v, f_sharpe_mom_v, f_trend_persist_v,
                 f_sfp_bull_v, f_sfp_bear_v)
            )
            if any(
                value is not None
                for value in (
                    _quality_score_v,
                    _quality_label_v,
                    _quality_detail_v,
                    _journey_stage_v,
                    _journey_age_v,
                    _journey_key_v,
                )
            ):
                c.execute(
                    """
                    UPDATE scan_signals
                    SET quality_score=?, quality_label=?, quality_detail=?,
                        journey_stage=?, journey_age=?, journey_key=?
                    WHERE scan_date=? AND symbol=? AND scan_type=?
                    """,
                    (
                        _quality_score_v,
                        _quality_label_v,
                        _quality_detail_v,
                        _journey_stage_v,
                        _journey_age_v,
                        _journey_key_v,
                        today,
                        symbol_db,
                        scan_type,
                    ),
                )
            _journey_payload = row.get('_journey')
            if isinstance(_journey_payload, dict) and scan_type == "rsi_pozitif_uyumsuzluk":
                _signal_row = c.execute(
                    """
                    SELECT id FROM scan_signals
                    WHERE scan_date=? AND symbol=? AND scan_type=?
                    """,
                    (today, symbol_db, scan_type),
                ).fetchone()
                if _signal_row:
                    upsert_rsi_journey(
                        conn,
                        int(_signal_row[0]),
                        symbol_db,
                        str(row.get("Sinyal_Tarihi") or today),
                        _journey_payload,
                    )
        assign_event_metadata_for_date(conn, scan_type, today, previous_run)
        conn.commit()
        conn.close()
        _write_guard.__exit__(None, None, None)
        _write_guard = None
        return True
    except sqlite3.OperationalError as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        if _write_guard is not None:
            _write_guard.__exit__(type(e), e, e.__traceback__)
            _write_guard = None
        if 'locked' in str(e).lower() and _lock_retry < 3:
            import time as _lt
            _lt.sleep(8 * (_lock_retry + 1))   # 8-16-24 sn — backtest yazım penceresini atlat
            return log_scan_signal(scan_type, df_result, category, _lock_retry + 1)
        logging.warning(f"[log_scan_signal] KİLİT/HATA (deneme {_lock_retry+1}) — scan_type={scan_type}: {e}")
        return False
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        if _write_guard is not None:
            _write_guard.__exit__(type(e), e, e.__traceback__)
            _write_guard = None
        logging.warning(f"[log_scan_signal] HATA — scan_type={scan_type}: {e}")
        return False

def backfill_signal_returns():
    """
    scan_signals tablosundaki sinyallerin 1-20 günlük getirilerini hesaplar ve
    signal_returns tablosuna yazar. INSERT — zaten varsa atlar (UNIQUE kısıt).
    Master Scan başında otomatik çağrılır; parquet cache üzerinden çalışır,
    internet isteği yapmaz. Her çalışmada en fazla 60 sembol işler.

    FIX (31 May 2026): N+1 darboğazı kaldırıldı. Eski sürüm her sinyal için
    get_safe_historical_data() çağırıyordu → her çağrı içeride get_live_price()
    (yfinance API hit) yapıp ~2sn sürüyordu. 60 sembol × 2sn = 2 dakika.
    Yeni sürüm doğrudan parquet'ten okur (canlı fiyat gereksiz), tek SQLite
    transaction kullanır. Aynı iş ~3-5sn'ye iniyor.

    Return: (dolduruldu, atlandı) tuple
    """
    today = datetime.now(_TZ_ISTANBUL).date()
    MAX_SYMBOLS = 60

    try:
        conn = sqlite3.connect(DB_FILE, timeout=30)
        # signal_returns'te hiç satırı olmayan scan_signals satırlarını bul
        pending = pd.read_sql('''
            SELECT ss.id, ss.symbol, ss.scan_type, ss.scan_date,
                   ss.entry_price, ss.category, ss.bias
            FROM scan_signals ss
            LEFT JOIN signal_returns sr ON ss.id = sr.signal_id
            WHERE sr.signal_id IS NULL
              AND COALESCE(ss.is_event_start, 1) = 1
              AND date(ss.scan_date) <= date('now', '-1 day')
            ORDER BY ss.scan_date DESC
            LIMIT ?
        ''', conn, params=(MAX_SYMBOLS,))
        conn.close()
    except Exception:
        return (0, 0)

    if pending.empty:
        return (0, 0)

    # Doğrudan parquet okuma — get_live_price/yfinance bypass.
    # Backfill geçmiş veri kullanır, bölünme/canlı fiyat kontrolü gereksiz.
    def _read_parquet_fast(ticker, category):
        try:
            _ticker = str(ticker or "").strip()
            _is_bist = (
                "BIST" in str(category or "").upper()
                or _ticker.upper().startswith(("XU", "XB", "XT", "XY", "XK", "XG", "XI", "XUS"))
                or _ticker.upper().endswith(".IS")
            )
            _ct = _ticker if not _is_bist else (
                _ticker if _ticker.upper().endswith(".IS") else f"{_ticker}.IS"
            )
            _fp = os.path.join(CACHE_DIR, f"{_ct}_1d.parquet")
            if not os.path.exists(_fp):
                return None
            _dfp = pd.read_parquet(_fp)
            # Split-düzeltmesi (21 Haz 2026) — işlenmemiş bölünme backtest getirisini
            # bozar (arada kalan split = sahte -%78 getiri). BIST hisse, idempotent.
            if (".IS" in _ct or ".IS" in ticker) and not _ct.startswith(("XU", "XB", "XT", "XY")):
                _dfp = _apply_split_adjustments(_dfp)
            return _dfp
        except Exception:
            return None

    # Sembol bazında parquet'i tek seferde önbelleğe al (aynı sembolün
    # birden çok sinyali varsa tekrar okumayı engeller).
    df_cache = {}
    filled = 0
    skipped = 0

    # Tek SQLite connection (eskiden döngüde açılıp kapatılıyordu).
    _write_guard = database_write_lock("return_backfill")
    _write_guard.__enter__()
    conn = sqlite3.connect(DB_FILE, timeout=60)
    c = conn.cursor()
    try:
        for _, sig in pending.iterrows():
            try:
                signal_date = pd.to_datetime(sig['scan_date']).date()
                if (today - signal_date).days < 1:
                    skipped += 1
                    continue

                sym = sig['symbol']
                if sym not in df_cache:
                    df_cache[sym] = _read_parquet_fast(sym, sig.get('category', ''))
                df_h = df_cache[sym]
                if df_h is None or df_h.empty:
                    skipped += 1
                    continue
                df_h = df_h.sort_index()

                sig_ts  = pd.Timestamp(sig['scan_date'])
                idx_pos = int(df_h.index.searchsorted(sig_ts))
                if idx_pos >= len(df_h):
                    skipped += 1
                    continue

                _entry_info = resolve_next_open_entry(
                    df_h, sig['scan_date'], bias=sig.get('bias', 'bullish'),
                    apply_bist_limit=(
                        not str(sig.get('category', '') or '').strip()
                        or 'BIST' in str(sig.get('category', '') or '').upper()
                        or str(sig['symbol']).upper().endswith('.IS')
                    ),
                    max_locked_sessions=3,
                )
                if not str(_entry_info.get('status', '')).startswith('filled'):
                    skipped += 1
                    continue
                entry = float(_entry_info['entry_price'])
                entry_pos = int(_entry_info['entry_pos'])

                for day_offset in range(1, 21):
                    # Gün 1 = gerçekten girilebilen açılış seansının kapanışı.
                    fwd_idx = entry_pos + day_offset - 1
                    if fwd_idx >= len(df_h):
                        break
                    fwd_price = float(df_h['Close'].iloc[fwd_idx])
                    ret_pct   = round((fwd_price - entry) / entry * 100, 4)
                    c.execute('''
                        INSERT OR IGNORE INTO signal_returns
                        (signal_id, scan_type, symbol, signal_date,
                         entry_price, day_offset, close_price, return_pct, category)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (int(sig['id']), sig['scan_type'], sig['symbol'],
                          sig['scan_date'], entry, day_offset,
                          fwd_price, ret_pct, sig.get('category', '')))
                    filled += 1
            except Exception:
                skipped += 1
                continue
        conn.commit()
    finally:
        conn.close()
        _write_guard.__exit__(None, None, None)

    return (filled, skipped)


# ── BİRLEŞİK FORMASYON MOTORU köprüsü (20 Tem 2026) ────────────────────────────
# scan_chart_patterns HEM tek-hisse panelini (5 çağrı yeri) HEM Master Scan'i besler → TEK KAYNAK.
# Flag AÇIK: her hisse için formasyon_core.analyze çalışır, eski dağınık detektörler ATLANIR (bunlar
# insan-etiketinde 5/19'du, kalibre birleşik motor yerine geçer). Flag KAPALI: hiçbir şey değişmez.
# ⚠️ Birleşik motor ŞEKİL-kalibre; return-backtest'i yok → scan_signals'a yazılıp temiz backtest
# birikecek. Detay: memory/project_formation_recalibration.md.
# 20 Tem 2026 Adım 4: varsayılan AÇIK (birleşik motor canlı). GERİ DÖNÜŞ tek satır:
# ya bu varsayılanı '0' yap, ya da ortam değişkeni BIRLESIK_ENGINE=0 ver (env her zaman kazanır).
_BIRLESIK_ON = os.environ.get('BIRLESIK_ENGINE', '1') == '1'

_BIR_SHAPE = {'tobo': ('🧛', 'TOBO'), 'fincan': ('☕', 'FİNCAN-KULP'),
              'ucgen': ('📐', 'YÜKSELEN ÜÇGEN'), 'dtri': ('📉', 'ALÇALAN ÜÇGEN'),
              'simetrik': ('🎯', 'SİMETRİK ÜÇGEN'),
              'kama_dusen': ('🔻', 'ALÇALAN KAMA'), 'kama_yuksek': ('🔺', 'YÜKSELEN KAMA'),
              'taban': ('🧱', 'TABAN')}
_BIR_STATE = {'YAKIN': 'Yaklaşıyor', 'KIRILDI': 'Kırılım Bölgesinde', 'UZAMIS': 'Kırıldı, Uzadı',
              'ERKEN': 'Oluşuyor (Erken)', 'FAIL': 'Bozuldu'}
_BIR_SCORE = {'KIRILDI': 90, 'YAKIN': 82, 'UZAMIS': 60, 'FAIL': 45, 'ERKEN': 70}


def _birlesik_pattern_row(symbol, df, close, volume, curr_price, sma200):
    """formasyon_core sonucunu scan_chart_patterns satır+ChartData formatına çevirir (tek kaynak
    adaptörü). ChartData type='birlesik' → app.py tek jenerik çizim dalıyla render eder (çizgi +
    temaslar + baş/omuz + durum). Bar indeksleri df.tail(500) çerçevesine göre tarihe çevrilir."""
    import formasyon_core
    r = formasyon_core.analyze(df, symbol)
    if not r:
        return None
    idx = df.tail(500).index
    def _d(i):
        i = max(0, min(int(i), len(idx) - 1)); return str(idx[i].date())
    shape, state = r['shape'], r['state']
    emoji, shname = _BIR_SHAPE.get(shape, ('🔺', shape.upper()))
    lvl = float(r.get('display_level', r['level']))
    name = f"{emoji} {shname} — {_BIR_STATE.get(state, state)}"
    info = r.get('info', {})
    chart_d = {
        "type": "birlesik", "shape": shape, "state": state, "level": lvl,
        "date_start": _d(r['fs']),
        "touch_dates":  [_d(i) for i, _ in r['touches']],
        "touch_prices": [float(v) for _, v in r['touches']],
        # pivot_dates: app.py'nin formasyon-yaşı/hacim kodu bunu okur (uyumluluk)
        "pivot_dates":  [_d(r['fs'])] + [_d(i) for i, _ in r['touches']],
    }
    if 'hd_i' in info:
        chart_d["head_date"] = _d(info['hd_i']); chart_d["head_price"] = float(info['head'])
    if 'ls_i' in info:
        chart_d["ls_date"] = _d(info['ls_i']);   chart_d["ls_price"] = float(info['ls'])
    if 'res_now' in info:
        chart_d["res_now"] = float(info['res_now'])
    desc = f"Ana çizgi (destek/direnç): {lvl:.2f}"
    if 'res_now' in info:
        desc += f" | Alçalan direnç: {info['res_now']:.2f}"
    desc += f" | Durum: {_BIR_STATE.get(state, state)}"
    score = _BIR_SCORE.get(state, 70)
    avg_vol = float(volume.iloc[-20:].mean())
    if avg_vol < 5_000_000:
        name += " (⚠️ SIĞ TAHTA)"; score -= 5
    if not np.isnan(sma200) and curr_price < sma200:
        name += " (⚠️ SMA200 Altında)"; score -= 10
    return {"Sembol": symbol, "Fiyat": curr_price, "Formasyon": name, "Detay": desc,
            "Skor": int(score), "Hacim": float(volume.iloc[-1]), "ChartData": chart_d}


# ── FORMASYON MOTORU BİRLEŞTİRME (27 Tem 2026) ───────────────────────────────
# formasyon_v2'nin PatternCandidate'ini _birlesik_pattern_row ile AYNI satır+ChartData
# formatına çevirir → app.py render / terazi / AI koduna DOKUNMADAN v2'ye geçilir.
# Motor seçimi FORMASYON_ENGINE bayrağı (varsayılan 'v2'). Rollback: env FORMASYON_ENGINE=core.
# formasyon_core + eski V6 zigzag EMEKLİ (silinmedi — bayrakla geri dönülebilir).
_FORMASYON_ENGINE = os.environ.get('FORMASYON_ENGINE', 'v2').lower()

_V2_SHAPE = {"TOBO": "tobo", "FİNCAN_KULP": "fincan", "FİNCAN_ADAYI": "fincan",
             "YÜKSELEN_ÜÇGEN": "ucgen", "ALÇALAN_ÜÇGEN": "dtri",
             "SİMETRİK_ÜÇGEN": "simetrik",
             "ALÇALAN_KAMA": "kama_dusen", "YÜKSELEN_KAMA": "kama_yuksek"}
# Yeni birleşen tipler (10 Ağu 2026): iki kenarı da EĞİMLİ → tek yatay çizgi yetmez.
# Köprü chart_d'ye açık 'bias' + iki kenarın (line_top/line_bot) uç fiyatlarını geçer;
# app render + hikaye shape=='dtri' binary'si yerine 'bias'ı okur (geriye-uyumlu fallback).
_V2_SLOPED_SHAPES = {"simetrik", "kama_dusen", "kama_yuksek"}
# VIP Formasyon (golden agent) v2 göçü (10 Ağu 2026) — v2 pattern adı → görünen etiket.
_V2_VIP_LABEL = {
    "FİNCAN_KULP": "☕ Fincan-Kulp", "TOBO": "🧛 TOBO",
    "YÜKSELEN_ÜÇGEN": "📐 Yükselen Üçgen", "ALÇALAN_ÜÇGEN": "📉 Alçalan Üçgen",
    "SİMETRİK_ÜÇGEN": "🎯 Simetrik Üçgen",
    "ALÇALAN_KAMA": "🔻 Alçalan Kama", "YÜKSELEN_KAMA": "🔺 Yükselen Kama",
}
_V2_STATE = {"OLUŞUYOR": "ERKEN", "YAKIN": "YAKIN",
             "KIRILIM_ADAYI": "KIRILDI", "KIRILIM_DOĞRULANDI": "KIRILDI",
             "YENİDEN_TEST": "KIRILDI", "UZAMIŞ": "UZAMIS",
             "SÜRESİ_DOLDU": "UZAMIS", "TAMAMLANDI": "UZAMIS", "GEÇERSİZ": "FAIL"}
# Yaşam döngüsü (lifecycle) — app.py rozeti (~5871) + AI prompt (~13390) chart_d['stage']
# okur; formasyon_core hiç set etmiyordu (özellik 20 Tem'den beri ölü). v2'nin zengin aşamaları
# düzgün doldurur. Aktif aşamalar: form/break/retest (inactive olanlar zaten gösterilmez).
_V2_LIFECYCLE = {"OLUŞUYOR": "form", "YAKIN": "form",
                 "KIRILIM_ADAYI": "break", "KIRILIM_DOĞRULANDI": "break",
                 "YENİDEN_TEST": "retest", "UZAMIŞ": "extended",
                 "SÜRESİ_DOLDU": "extended", "TAMAMLANDI": "completed", "GEÇERSİZ": "failed"}


def _v2_pattern_row(symbol, df, close, volume, curr_price, sma200):
    """formasyon_v2 sonucunu _birlesik_pattern_row ile AYNI satır+ChartData formatına çevirir.
    Şekil çizim anahtarları (touch_/head_/ls_/res_now) app.py 'birlesik' render'ının okuduğu
    formatla birebir. v2 bir şey bulamazsa None (çekirdeğe düşmez — tek motor v2)."""
    try:
        import formasyon_v2 as _fv2
        rep = _fv2.analyze_formations(df.tail(500), ticker=symbol, timeframe="1d")
    except Exception:
        return None
    if not rep or not getattr(rep, "patterns", None):
        return None
    # Haritalanabilir ilk (en yüksek kaliteli) formasyon — patterns[0] haritada değilse atlama.
    cand = next((c for c in rep.patterns if c.pattern in _V2_SHAPE), None)
    if cand is None:
        return None
    shape = _V2_SHAPE[cand.pattern]
    state = _V2_STATE.get(cand.stage, "ERKEN")
    lns = {ln.role: ln for ln in cand.lines}
    first, multi = {}, {}
    for p in cand.points:
        first.setdefault(p.role, p)
        multi.setdefault(p.role, []).append(p)

    def _d(ts):
        return str(pd.Timestamp(ts).date())

    _bias = "bearish" if cand.direction == "bearish" else "bullish"
    # LEVEL = KIRILIM (trigger) çizgisi. ucgen üst direnç, dtri alt destek;
    # kama/simetrik kırılım yönüne göre (boğa=üst kenar, ayı=alt kenar).
    line = {"tobo": "boyun_çizgisi", "fincan": "fincan_ağzı",
            "ucgen": "üst_sınır", "dtri": "alt_sınır",
            "kama_dusen": "üst_sınır", "kama_yuksek": "alt_sınır",
            "simetrik": "üst_sınır" if _bias == "bullish" else "alt_sınır"}.get(shape)
    _ln = lns.get(line)
    level = float(_ln.end_price) if _ln else float(cand.trigger)

    chart_d = {"type": "birlesik", "shape": shape, "state": state, "bias": _bias,
               "level": round(level, 2), "date_start": _d(cand.start_time)}
    _lc = _V2_LIFECYCLE.get(cand.stage)                      # yaşam döngüsü rozeti + AI uyarısı
    if _lc:
        chart_d["stage"] = _lc
        chart_d["stage_days"] = int(cand.metrics.get("breakout_age_bars", 0) or 0)
    if shape == "tobo":
        tp = [first[r] for r in ("boyun_1", "boyun_2") if r in first]
    elif shape == "fincan":
        tp = [first[r] for r in ("sol_dudak", "sağ_dudak") if r in first]
    elif shape == "ucgen":
        tp = multi.get("üst_temas", [])
    elif shape == "dtri":
        tp = multi.get("alt_temas", [])
    else:                                                    # simetrik / kama — iki kenarın teması
        tp = multi.get("üst_temas", []) + multi.get("alt_temas", [])
    chart_d["touch_dates"] = [_d(p.time) for p in tp]
    chart_d["touch_prices"] = [float(p.price) for p in tp]
    chart_d["pivot_dates"] = [_d(cand.start_time)] + chart_d["touch_dates"]
    if shape == "tobo":
        if "baş" in first:
            chart_d["head_date"] = _d(first["baş"].time); chart_d["head_price"] = float(first["baş"].price)
        if "sol_omuz" in first:
            chart_d["ls_date"] = _d(first["sol_omuz"].time); chart_d["ls_price"] = float(first["sol_omuz"].price)
    if shape == "dtri" and lns.get("üst_sınır"):
        chart_d["res_now"] = float(lns["üst_sınır"].end_price)
    # EĞİMLİ tipler: iki kenarı da (uç fiyatlar) geçir → app iki eğimli çizgi çizer.
    if shape in _V2_SLOPED_SHAPES:
        _t, _b = lns.get("üst_sınır"), lns.get("alt_sınır")
        if _t:
            chart_d["line_top"] = [round(float(_t.start_price), 2), round(float(_t.end_price), 2)]
        if _b:
            chart_d["line_bot"] = [round(float(_b.start_price), 2), round(float(_b.end_price), 2)]

    # breakout_state (0/1/2/3) — çekirdek motorda vardı, v2'ye taşındı (31 Tem 2026):
    # v2 varsayılan motor olunca f_breakout_state snapshot'ı boşalmıştı (heartbeat anomali yakaladı).
    # Ana çizgi (level) = kırılım sınırı; _detect_breakout_state ile ölç, ChartData'ya yaz.
    try:
        _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(level))
        chart_d["breakout_state"]     = int(_bk_st)
        chart_d["breakout_gap_pct"]   = float(_bk_gap)
        chart_d["breakout_vol_ratio"] = float(_bk_vol)
    except Exception:
        pass

    emoji, shname = _BIR_SHAPE.get(shape, ('🔺', shape.upper()))
    name = f"{emoji} {shname} — {_BIR_STATE.get(state, state)}"
    desc = f"Ana çizgi: {level:.2f} · v2 kalite {cand.quality_score:.0f}/100 · durum {_BIR_STATE.get(state, state)}"
    if shape == "dtri" and "res_now" in chart_d:
        desc += f" · inen direnç {chart_d['res_now']:.2f}"
    score = _BIR_SCORE.get(state, 70)
    avg_vol = float(volume.iloc[-20:].mean())
    if avg_vol < 5_000_000:
        name += " (⚠️ SIĞ TAHTA)"; score -= 5
    if not np.isnan(sma200) and curr_price < sma200:
        name += " (⚠️ SMA200 Altında)"; score -= 10
    return {"Sembol": symbol, "Fiyat": curr_price, "Formasyon": name, "Detay": desc,
            "Skor": int(score), "Hacim": float(volume.iloc[-1]), "ChartData": chart_d}


def scan_chart_patterns(asset_list):
    """
    V6: ZIGZAG TABANLI FORMASYON MOTORU
    - Gürültüyü eler, yalnızca anlamlı salınımları (zigzag iskelet) kullanır.
    - İnsan gözünün gördüğü şekli sayısal olarak tespit eder.
    - TOBO: L,H,L*,H,L — son 5 anlamlı pivot üzerinden
    - Fincan-Kulp: H,L,H≈ilk,L(kulp) — son 4 anlamlı pivot üzerinden
    - 2 yıllık veri ile büyük formasyonlar kaçmaz.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    current_cat = st.session_state.get('category', 'S&P 500')
    benchmark = get_benchmark_data(current_cat)

    # ---------------------------------------------------------------
    # ZIGZAG ALGORİTMASI — %threshold kadar ters dönen hareketleri kaydet.
    # İnsan gözünün grafik iskeleti olarak gördüğü anlamlı tepeler/dipler.
    # Döndürür: [(bar_index, fiyat, 'H'/'L'), ...]
    # ---------------------------------------------------------------
    def zigzag_pivots(close, threshold=0.04):
        pivots = []
        if len(close) < 10: return pivots
        direction = None
        last_i, last_p = 0, float(close.iloc[0])
        for i in range(1, len(close)):
            p = float(close.iloc[i])
            if direction is None:
                if p >= last_p * (1 + threshold):
                    direction = 'up'; last_i, last_p = i, p
                elif p <= last_p * (1 - threshold):
                    direction = 'down'; last_i, last_p = i, p
            elif direction == 'up':
                if p > last_p:
                    last_i, last_p = i, p
                elif p <= last_p * (1 - threshold):
                    pivots.append((last_i, last_p, 'H'))
                    direction = 'down'; last_i, last_p = i, p
            else:
                if p < last_p:
                    last_i, last_p = i, p
                elif p >= last_p * (1 + threshold):
                    pivots.append((last_i, last_p, 'L'))
                    direction = 'up'; last_i, last_p = i, p
        # Son segment
        if direction == 'up':   pivots.append((last_i, last_p, 'H'))
        elif direction == 'down': pivots.append((last_i, last_p, 'L'))
        return pivots

    def process_single_pattern(symbol):
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: return None
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            if len(df) < 150: return None

            close      = df['Close']
            high       = df['High']
            low        = df['Low']
            open_      = df['Open']
            volume     = df['Volume']
            curr_price = float(close.iloc[-1])
            bar_total  = len(df)

            sma200 = close.rolling(200).mean().iloc[-1]

            # Mansfield RS
            mansfield_val = 0.0
            if benchmark is not None:
                try:
                    common = close.index.intersection(benchmark.index)
                    if len(common) > 55:
                        rs_r = close.reindex(common) / benchmark.reindex(common)
                        rs_m = rs_r.rolling(50).mean()
                        m = ((rs_r / rs_m) - 1) * 10
                        mansfield_val = float(m.iloc[-1]) if not np.isnan(m.iloc[-1]) else 0.0
                except: pass

            # Ani dump filtresi
            prev_close = float(close.iloc[-2])
            if (curr_price - prev_close) / prev_close <= -0.025: return None

            # BİRLEŞİK MOTOR (flag'li) — açıksa eski detektör kaskadı ATLANIR, tek kaynak burası.
            # 27 Tem 2026: motor seçimi FORMASYON_ENGINE. Varsayılan v2 (birleştirme); core rollback.
            if _BIRLESIK_ON:
                if _FORMASYON_ENGINE == 'v2':
                    return _v2_pattern_row(symbol, df, close, volume, curr_price, sma200)
                return _birlesik_pattern_row(symbol, df, close, volume, curr_price, sma200)

            pattern_found = False
            pattern_name  = ""
            desc          = ""
            base_score    = 0
            chart_d       = None   # mini grafik verisi (sadece Fincan-Kulp / TOBO)

            # ---------------------------------------------------------------
            # ZIGZAG İSKELETİ — %4 eşikli (insan gözüne yakın)
            # ---------------------------------------------------------------
            # Zigzag QML + 3 Drive tarafından kullanılır (Fincan/TOBO artık swing tabanlı).
            zz       = zigzag_pivots(close, threshold=0.04)
            zz_chron = sorted(zz, key=lambda x: x[0])   # Kronolojik sıra
            zz_l     = [(i, p) for (i, p, t) in zz_chron if t == 'L']

            close_np = close.values.astype(float)
            vol_np   = volume.values.astype(float)

            # 11 — ADAY HAVUZU: her detektör bulduğunu buraya bırakır, sonda
            # en yüksek puanlı seçilir (eskiden ilk bulunan kazanıyordu).
            _cands = []

            # ---------------------------------------------------------------
            # WICK/BODY FİLTRESİ — Gürültülü bölgeleri eliyor
            # Formasyon bölgesindeki barların fitil/gövde oranını kontrol eder.
            # Median fitil > 2 × median gövde ise formasyon geçersiz sayılır.
            # ---------------------------------------------------------------
            def is_clean_zone(start_idx, end_idx):
                """True döndürürse bölge temiz, False ise gürültülü/fitilli."""
                try:
                    s = max(0, start_idx)
                    e = min(bar_total, end_idx + 1)
                    if e - s < 5: return True  # Çok kısa bölge, filtre uygulama
                    o_arr = open_.iloc[s:e].values.astype(float)
                    c_arr = close.iloc[s:e].values.astype(float)
                    h_arr = high.iloc[s:e].values.astype(float)
                    l_arr = low.iloc[s:e].values.astype(float)
                    bodies = np.abs(c_arr - o_arr)
                    wicks  = (h_arr - l_arr) - bodies
                    med_body = np.median(bodies)
                    med_wick = np.median(wicks)
                    if med_body < 1e-9: return False  # Doji bölgesi — geçersiz
                    return med_wick <= 2.0 * med_body
                except:
                    return True  # Hata durumunda filtreyi geç

            # ---------------------------------------------------------------
            # 1. BOĞA BAYRAĞI — Kısa vadeli, ham fiyat bazlı (zigzag gerekmez)
            # ---------------------------------------------------------------
            if not pattern_found:
                pole_start = float(close.iloc[-20])
                pole_end   = float(close.iloc[-6])
                pole       = (pole_end - pole_start) / pole_start
                flag_h     = float(high.iloc[-5:].max())
                flag_l     = float(low.iloc[-5:].min())
                tight      = (flag_h - flag_l) / flag_l if flag_l > 0 else 1
                retrace    = (pole_end - curr_price) / (pole_end - pole_start) if (pole_end - pole_start) > 0 else 1
                if (pole > 0.15 and tight < 0.06
                        and retrace < 0.50
                        and curr_price >= flag_l * 0.99
                        and curr_price >= flag_h * 0.98):
                    chart_d = {
                        "type": "flag",
                        "date_start": str(close.index[max(0, bar_total - 22)].date()),
                        "flag_h": float(flag_h),
                        "flag_l": float(flag_l),
                        "pole_end_date": str(close.index[bar_total - 6].date()),
                    }
                    pattern_found = True
                    pattern_name  = "🚩 BOĞA BAYRAĞI"
                    base_score    = 85
                    desc = f"Direk: %{pole*100:.1f} | Sıkışma: %{tight*100:.1f} | Geri Alım: %{retrace*100:.0f}"
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # YARDIMCI: Swing High / Low tespiti (lookback bar sol-sağ)
            # ---------------------------------------------------------------
            def find_swings(series, lookback=8):
                highs, lows = [], []
                arr = series.values.astype(float)
                n   = len(arr)
                for i in range(lookback, n - lookback):
                    w = arr[i - lookback: i + lookback + 1]
                    if arr[i] >= w.max() - 1e-9:
                        highs.append((i, arr[i]))
                    if arr[i] <= w.min() + 1e-9:
                        lows.append((i, arr[i]))
                return highs, lows

            # 7 — Pivotlar kapanış yerine fitil uçlarından: boyun/dip seviyeleri
            # TradingView'de gözle görülen gerçek tepe/dip fitilleriyle örtüşür.
            sw_h, _ = find_swings(high, lookback=8)
            _, sw_l = find_swings(low, lookback=8)
            # 10 — Uyarlanabilir anlamlılık eşiği: hissenin kendi volatilitesine
            # göre küçük salınım pivotları budanır (sakin hissede eşik düşer,
            # oynak hissede yükselir; sabit %4 herkese uymuyordu).
            _adapt_thr = pattern_core.adaptive_threshold(close_np)
            sw_h, sw_l = pattern_core.prune_pivots(sw_h, sw_l, _adapt_thr)
            sw_h_y = [(i, v) for i, v in sw_h if i >= bar_total - 252]  # son 12 ay
            sw_l_y = [(i, v) for i, v in sw_l if i >= bar_total - 252]

            # ---------------------------------------------------------------
            # 2. FİNCAN-KULP — Swing tabanlı + polinom U-şekil doğrulaması
            # Min: 40 bar (~2 ay), Max: 252 bar (12 ay), R/R >= 1.0
            # Son pivot 60 günden eski ise gösterilmez.
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 1:
                for ri in range(len(sw_h_y) - 1, 0, -1):
                    if pattern_found: break
                    sh2_i, sh2_v = sw_h_y[ri]           # Sağ rim
                    # 18 Tem: sağ-rim ≤60bar tazelik kapısı KALDIRILDI (tespit tazelikten AYRI;
                    # gerçek fincanların sağ rimi kulp/kırılım nedeniyle doğal olarak eskir).
                    for li in range(ri - 1, max(ri - 12, -1), -1):
                        sh1_i, sh1_v = sw_h_y[li]       # Sol rim
                        cup_dur = sh2_i - sh1_i
                        if not (40 <= cup_dur <= 252): continue
                        # 6 — Ön-trend şartı: fincan DEVAM formasyonudur, kupa
                        # öncesi belirgin yükseliş olmalı (yoksa yatay çöp salınım).
                        if not pattern_core.cup_pretrend_ok(close_np, sh1_i): continue
                        # Cup içi en derin swing low
                        cup_lows = [(i, v) for i, v in sw_l_y if sh1_i < i < sh2_i]
                        if not cup_lows: continue
                        sl_i, sl_v = min(cup_lows, key=lambda x: x[1])
                        # Derinlik ve rim hizası
                        depth = (sh1_v - sl_v) / sh1_v
                        if not (0.12 <= depth <= 0.55): continue
                        # Rim hizalaması (18 Tem kalibrasyon: 6%→8.5%, AKSEN %8.3 gerçek fincandı)
                        if abs(sh1_v - sh2_v) / sh1_v > pattern_core.PC['cup_rim']: continue
                        # U-şekil: polinom fit (R² > 0.72, konkav yukarı)
                        try:
                            cup_arr = close.iloc[sh1_i:sh2_i + 1].values.astype(float)
                            if len(cup_arr) < 10: continue
                            # 16 Haz 2026 — Polinom fit 5g EMA üzerinde yapılır.
                            # Volatil dipli klasik fincanlarda (TOASO: R²=0.69 ham, ~%23 derinlik,
                            # %1 rim hizası, akademik fincan ama trend rallisi gürültüsü polinomu
                            # bozuyordu) yakalama oranı yükselir; R² eşiği bozulmadan.
                            # Slope/shape kontrolleri (_validate_cup_shape) ham cup_arr ile devam eder.
                            cup_smooth = pd.Series(cup_arr).ewm(span=5, adjust=False).mean().values
                            xf  = np.linspace(0, 1, len(cup_arr))
                            cf  = np.polyfit(xf, cup_smooth, 2)
                            yp  = np.polyval(cf, xf)
                            ss_res = np.sum((cup_smooth - yp) ** 2)
                            ss_tot = np.sum((cup_smooth - cup_smooth.mean()) ** 2)
                            r2  = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                            if cf[0] <= 0: continue  # Konkav yukarı zorunlu
                        except: continue
                        # FIX (30 May 2026): Şekil doğrulaması — dip ortada + sol iniş/sağ çıkış + R²≥0.78
                        if not _validate_cup_shape(cup_arr, sh1_i, sl_i, sh2_i, r2): continue
                        # Wick/Body filtresi: fincan bölgesi gürültülü değil mi?
                        if not is_clean_zone(sh1_i, sh2_i): continue
                        # Handle: sh2'den sonraki ilk swing low
                        h_lows = [(i, v) for i, v in sw_l_y if i > sh2_i]
                        if h_lows:
                            hl_i, hl_v = h_lows[0]
                        else:
                            after = close.iloc[sh2_i:]
                            if len(after) < 3: continue
                            rel  = int(after.values.argmin())
                            hl_i = sh2_i + rel
                            hl_v = float(after.iloc[rel])
                        if not (hl_v > sl_v + (sh2_v - sl_v) * 0.35): continue  # Kulp üst %65'te
                        if not (hl_v > sh2_v * pattern_core.PC['cup_handle_lo']): continue  # kulp fazla derin değil (0.82→0.81)
                        # ---- İSKELET TAM. Durumu tazelik/actionability'den AYRI sınıflandır (18 Tem).
                        # scan_chart_patterns TEK-HİSSE panel → TÜM durumlar (extended/failed dahil).
                        # handle_dur + R/R yalnız actionable (form/break/retest) durumlara uygulanır.
                        target = sh2_v + (sh2_v - sl_v)
                        _pb = pattern_core.detect_post_breakout(close_np, vol_np, sh2_v)
                        dist = ((sh2_v - curr_price) / sh2_v * 100) if curr_price < sh2_v else 0
                        retesting = False
                        _cup_state = None
                        if _pb['failed']:
                            _cup_state = 'failed'                       # kırılım bozuldu
                        elif curr_price >= target:
                            # HEDEFE ULAŞTI — hareket tamamlandı, artık fırsat değil (geçmişi açıklar).
                            # "extended"ten AYRILDI (21 Tem 2026): önceden ikisi tek 'uzadı'ydı.
                            # Yalnız kırılım YAKINSA göster (yoksa bayat) — extended ile aynı guard.
                            _above = np.where(close_np[sh2_i + 1:] > sh2_v * 1.01)[0]
                            if len(_above) and (bar_total - 1 - (sh2_i + 1 + int(_above[0]))) <= pattern_core.PC['cup_ext_recent']:
                                _cup_state = 'completed'
                        elif curr_price > sh2_v * 1.10:
                            # UZAMIŞ — kırılım son cup_ext_recent barda ise göster (yoksa bayat, geç)
                            _above = np.where(close_np[sh2_i + 1:] > sh2_v * 1.01)[0]
                            if len(_above) and (bar_total - 1 - (sh2_i + 1 + int(_above[0]))) <= pattern_core.PC['cup_ext_recent']:
                                _cup_state = 'extended'
                        else:
                            retesting = _pb['retest']
                            breaking  = (not retesting) and sh2_v * 0.97 <= curr_price <= sh2_v * 1.10
                            forming   = (not retesting) and (not breaking) and curr_price < sh2_v * 0.97 \
                                        and curr_price >= hl_v * 0.98 and dist <= pattern_core.PC['form_max_dist']
                            if retesting or breaking or forming:
                                # ACTIONABLE → kulp süresi + R/R burada
                                if pattern_core.handle_dur_ok(sh2_i, bar_total, cup_dur) \
                                        and (target - curr_price) / max(curr_price - hl_v * 0.98, 0.01) >= 1.0:
                                    _cup_state = 'retest' if retesting else 'break' if breaking else 'form'
                        if _cup_state is None: continue
                        dur_months = max(1, round(cup_dur / 21))
                        if _cup_state == 'retest':
                            p_name = f"🎯 FİNCAN KULP RETEST ({dur_months} Ay) — Boyun Destek Testi"; base_score = 94
                        elif _cup_state == 'break':
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırılım Bölgesinde"; base_score = 92
                        elif _cup_state == 'form':
                            p_name = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — %{dist:.1f} kaldı"; base_score = 75
                        elif _cup_state == 'completed':
                            p_name = f"🏁 FİNCAN KULP ({dur_months} Ay) — Hedefe Ulaştı, Tamamlandı (geçmişi açıklar)"; base_score = 42
                        elif _cup_state == 'extended':
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırıldı, Uzadı (fiyat %{(curr_price/sh2_v-1)*100:.0f} boyun üstünde)"; base_score = 55
                        else:  # failed
                            p_name = f"⚠️ FİNCAN KULP ({dur_months} Ay) — Kırılım Başarısız (Bozuldu)"; base_score = 45
                        p_desc  = (f"Sol Rim: {sh1_v:.2f} | Dip: {sl_v:.2f} | Sağ Rim: {sh2_v:.2f} | "
                                   f"Kulp: {hl_v:.2f} | Hedef: {target:.2f} | R²: {r2:.2f}")
                        chart_d = {
                            "pivot_dates":  [str(close.index[sh1_i].date()),
                                             str(close.index[sl_i].date()),
                                             str(close.index[sh2_i].date()),
                                             str(close.index[min(hl_i, bar_total - 1)].date())],
                            "pivot_prices": [sh1_v, sl_v, sh2_v, hl_v],
                            "pivot_types":  ["H", "L", "H", "L"],
                            "neck": float(sh2_v),
                            "type": "cup",
                            "stage": _cup_state,   # yaşam döngüsü (21 Tem 2026) — rozet bunu okur
                            "stage_days": int(_pb.get('days_since', 0)),  # aşama yaşı (kaç gün oldu)
                        }
                        # 4 — Kırılım durumu artık fincanda da ölçülüyor (TOBO/W'de vardı)
                        _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(sh2_v))
                        chart_d["breakout_state"]     = int(_bk_st)
                        chart_d["breakout_gap_pct"]   = float(_bk_gap)
                        chart_d["breakout_vol_ratio"] = float(_bk_vol)
                        # 9 — Hacim imzası: sönümlenme + kulp dibinde tükenme + dönüş
                        _vs = pattern_core.volume_signature(vol_np, sh1_i, sh2_i, min(hl_i, bar_total - 1))
                        chart_d["vol_dip_ok"]    = _vs['dip_ok']
                        chart_d["vol_bounce_ok"] = _vs['bounce_ok']
                        chart_d["vol_fade_ok"]   = _vs['fade_ok']
                        chart_d["retest"]        = bool(retesting)
                        base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                        pattern_found = True
                        pattern_name  = p_name; desc = p_desc
                        break
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 3. TOBO — Swing tabanlı: 5 pivot L, H, L(derin), H, L
            # Min: 40 bar, Max: 252 bar, R/R >= 1.0
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 3:
                for i_rs in range(len(sw_l_y) - 1, 1, -1):
                    if pattern_found: break
                    sl3_i, sl3_v = sw_l_y[i_rs]             # Sağ omuz
                    if bar_total - sl3_i > 60: continue      # Son pivot 60 günden eski
                    for i_hd in range(i_rs - 1, 0, -1):
                        if pattern_found: break
                        sl2_i, sl2_v = sw_l_y[i_hd]         # Baş (en derin)
                        for i_ls in range(i_hd - 1, max(i_hd - 8, -1), -1):
                            sl1_i, sl1_v = sw_l_y[i_ls]     # Sol omuz
                            dur = sl3_i - sl1_i
                            if not (40 <= dur <= 252): continue
                            # Baş en derin olmalı
                            if not (sl2_v < sl1_v * 0.95 and sl2_v < sl3_v * 0.95): continue
                            # 6 — Ön-trend şartı: TOBO dip dönüş formasyonudur,
                            # öncesinde düşüş olmalı (baş önceki tepeden ≥%15 aşağıda).
                            if not pattern_core.tobo_pretrend_ok(close_np, sl1_i, sl2_v): continue
                            # Boyun noktaları: her omuz ile baş arasındaki en yüksek swing high
                            sh1_cands = [(i, v) for i, v in sw_h_y if sl1_i < i < sl2_i]
                            sh2_cands = [(i, v) for i, v in sw_h_y if sl2_i < i < sl3_i]
                            if not sh1_cands or not sh2_cands: continue
                            sh1_i, sh1_v = max(sh1_cands, key=lambda x: x[1])
                            sh2_i, sh2_v = max(sh2_cands, key=lambda x: x[1])
                            # FIX (30 May 2026): TOBO şekil doğrulaması — zaman simetrisi +
                            # eğimli boyun desteği + baş derinlik tabanı (ortak helper)
                            _tok, neck = _validate_tobo_shape(
                                sl1_i, sl1_v, sl2_i, sl2_v, sl3_i, sl3_v,
                                sh1_i, sh1_v, sh2_i, sh2_v, bar_total)
                            if not _tok: continue
                            if abs(sl1_v - sl3_v) / sl1_v > 0.15: continue  # Omuz simetrisi
                            recovery = (sl3_v - sl2_v) / (neck - sl2_v) if (neck - sl2_v) > 0 else 0
                            if recovery < 0.45: continue
                            # Wick/Body filtresi: TOBO bölgesi gürültülü değil mi?
                            if not is_clean_zone(sl1_i, sl3_i): continue
                            # R/R filtresi
                            target = neck + (neck - sl2_v)
                            risk   = max(curr_price - sl3_v * 0.98, 0.01)
                            rr     = (target - curr_price) / risk
                            if rr < 1.0: continue
                            # Durum tespiti — 12: retest / sahte kırılım katmanı
                            _pb = pattern_core.detect_post_breakout(close_np, vol_np, neck)
                            if _pb['failed']: continue   # sahte kırılım — formasyon bozuldu
                            dist = ((neck - curr_price) / neck * 100) if curr_price < neck else 0
                            retesting = _pb['retest']
                            breaking  = (not retesting) and neck * 0.97 <= curr_price <= neck * 1.08
                            # 3 — OLUŞAN için boyuna max %12 uzaklık (ARTMS tipi
                            # -%22 stoplu uzak adaylar elenir; Çift Dip kuralıyla aynı)
                            forming   = (not retesting) and (not breaking) and curr_price > sl3_v * 1.01 \
                                        and curr_price < neck * 0.96 \
                                        and dist <= pattern_core.PC['form_max_dist']
                            if not (retesting or breaking or forming): continue
                            dur_months = max(1, round(dur / 21))
                            if retesting:
                                p_name     = f"🎯 TOBO RETEST ({dur_months} Ay) — Boyun Destek Testi"
                                base_score = 94
                            elif breaking:
                                p_name     = f"🧛 TOBO ({dur_months} Ay) — Kırılım Bölgesinde"
                                base_score = 90
                            else:
                                p_name     = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — %{dist:.1f} kaldı"
                                base_score = 72
                            p_desc  = (f"Boyun: {neck:.2f} | Baş: {sl2_v:.2f} | "
                                       f"Sol/Sağ Omuz: {sl1_v:.2f}/{sl3_v:.2f} | "
                                       f"Hedef: {target:.2f} | Geri Alım: %{recovery*100:.0f}")
                            chart_d = {
                                "pivot_dates":  [str(close.index[sl1_i].date()),
                                                 str(close.index[sh1_i].date()),
                                                 str(close.index[sl2_i].date()),
                                                 str(close.index[sh2_i].date()),
                                                 str(close.index[sl3_i].date())],
                                "pivot_prices": [sl1_v, sh1_v, sl2_v, sh2_v, sl3_v],
                                "pivot_types":  ["L", "H", "L", "H", "L"],
                                "neck": float(neck),
                                "type": "tobo",
                                "stage": ('retest' if retesting else 'break' if breaking else 'form'),  # 21 Tem — rozet
                                "stage_days": int(_pb.get('days_since', 0)),
                            }
                            _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(neck))
                            chart_d["breakout_state"]     = int(_bk_st)
                            chart_d["breakout_gap_pct"]   = float(_bk_gap)
                            chart_d["breakout_vol_ratio"] = float(_bk_vol)
                            # 9 — Hacim imzası: omuz→baş→omuz sönümlenme + sağ omuzda tükenme
                            _vs = pattern_core.volume_signature(vol_np, sl1_i, sl3_i, sl3_i)
                            chart_d["vol_dip_ok"]    = _vs['dip_ok']
                            chart_d["vol_bounce_ok"] = _vs['bounce_ok']
                            chart_d["vol_fade_ok"]   = _vs['fade_ok']
                            chart_d["retest"]        = bool(retesting)
                            base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                            pattern_found = True
                            pattern_name  = p_name; desc = p_desc
                            break
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 3.5 ÇİFT DİP (W) — İki ~eşit dip + orta tepe (boyun) kırılımı
            # ---------------------------------------------------------------
            if not pattern_found:
                _db = _detect_double_bottom(sw_l_y, sw_h_y, curr_price, bar_total,
                                            is_index=_is_index_symbol(symbol))
                # Wick temizliği SADECE iki dip çevresinde (±6 bar) — uzun W span'i
                # doğal gürültü içerir; tam-span filtresi gerçek W'leri eler.
                _db_clean = bool(_db) and is_clean_zone(_db["d1_i"] - 6, _db["d1_i"] + 6) \
                                       and is_clean_zone(_db["d2_i"] - 6, _db["d2_i"] + 6)
                # 12 — retest / sahte kırılım katmanı
                _db_retest = False
                if _db and _db_clean:
                    _pb = pattern_core.detect_post_breakout(close_np, vol_np, _db['neck_v'])
                    if _pb['failed']:
                        _db = None               # sahte kırılım — formasyon bozuldu
                    else:
                        _db_retest = _pb['retest']
                if _db and _db_clean:
                    dur_months = max(1, round(_db["dur"] / 21))
                    if _db_retest:
                        pattern_name = f"🎯 ÇİFT DİP (W) RETEST ({dur_months} Ay) — Boyun Destek Testi"
                        base_score   = 94
                    elif _db["state"] == "break":
                        pattern_name = f"🔷 ÇİFT DİP (W) ({dur_months} Ay) — Kırılım Bölgesinde"
                        base_score   = 90
                    else:
                        pattern_name = f"⏳ OLUŞAN ÇİFT DİP (W) ({dur_months} Ay) — %{_db['dist']:.1f} kaldı"
                        base_score   = 72
                    desc = (f"Dip1: {_db['d1_v']:.2f} | Boyun: {_db['neck_v']:.2f} | "
                            f"Dip2: {_db['d2_v']:.2f} | Hedef: {_db['target']:.2f}")
                    chart_d = {
                        "pivot_dates":  [str(close.index[_db['d1_i']].date()),
                                         str(close.index[_db['neck_i']].date()),
                                         str(close.index[_db['d2_i']].date())],
                        "pivot_prices": [_db['d1_v'], _db['neck_v'], _db['d2_v']],
                        "pivot_types":  ["L", "H", "L"],
                        "neck": float(_db['neck_v']),
                        "type": "double_bottom",
                        "stage": ('retest' if _db_retest else 'break' if _db.get('state') == 'break' else 'form'),  # 21 Tem — rozet
                        "stage_days": int(_pb.get('days_since', 0)) if _db_retest or _db.get('state') == 'break' else 0,
                    }
                    _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(_db['neck_v']))
                    chart_d["breakout_state"]     = int(_bk_st)
                    chart_d["breakout_gap_pct"]   = float(_bk_gap)
                    chart_d["breakout_vol_ratio"] = float(_bk_vol)
                    # 9 — Hacim imzası (ikinci dipte tükenme + dönüş hacmi)
                    _vs = pattern_core.volume_signature(vol_np, _db['d1_i'], _db['d2_i'], _db['d2_i'])
                    chart_d["vol_dip_ok"]    = _vs['dip_ok']
                    chart_d["vol_bounce_ok"] = _vs['bounce_ok']
                    chart_d["vol_fade_ok"]   = _vs['fade_ok']
                    chart_d["retest"]        = bool(_db_retest)
                    base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 3.7 KAMA (Düşen=boğa / Yükselen=ayı) — yakınsayan trend çizgileri
            # Üçgenden ÖNCE: üçgen daha gevşek, kamayı yutmasın.
            # ---------------------------------------------------------------
            if not pattern_found:
                _wd = _detect_wedge(sw_h_y, sw_l_y, close, high, low, volume,
                                    curr_price, bar_total)
                if _wd and is_clean_zone(_wd["first_i"], bar_total - 1):
                    dur_months = max(1, round(_wd["dur"] / 21))
                    if _wd["kind"] == "falling":
                        if _wd["state"] == "break":
                            pattern_name = f"📉 DÜŞEN KAMA ({dur_months} Ay) — Yukarı Kırılım"
                            base_score   = 90
                        else:
                            pattern_name = f"⏳ OLUŞAN DÜŞEN KAMA ({dur_months} Ay) — %{_wd['dist']:.1f} kaldı"
                            base_score   = 74
                        desc = (f"Üst Direnç: {_wd['up_line']:.2f} | Alt Destek: {_wd['lo_line']:.2f} | "
                                f"Hedef: {_wd['target']:.2f} | R²: {_wd['r2_hi']:.2f}/{_wd['r2_lo']:.2f}"
                                + (" | 📉 Hacim daralıyor" if _wd['vol_drop'] else ""))
                        _wline = _wd["up_line"]
                    else:  # rising — ayı
                        if _wd["state"] == "break":
                            pattern_name = f"📈 YÜKSELEN KAMA ({dur_months} Ay) — Aşağı Kırılım (AYI)"
                            base_score   = 70
                        else:
                            pattern_name = f"⏳ OLUŞAN YÜKSELEN KAMA ({dur_months} Ay) — Tepe Riski %{_wd['dist']:.1f}"
                            base_score   = 55
                        desc = (f"Üst Direnç: {_wd['up_line']:.2f} | Alt Destek: {_wd['lo_line']:.2f} | "
                                f"Aşağı Hedef: {_wd['target']:.2f} | R²: {_wd['r2_hi']:.2f}/{_wd['r2_lo']:.2f}")
                        _wline = _wd["lo_line"]
                    chart_d = {
                        "type":       "wedge",
                        "kind":       _wd["kind"],
                        "date_start": str(close.index[max(0, _wd['first_i'])].date()),
                        "up_start":   float(_wd["up_start"]),
                        "lo_start":   float(_wd["lo_start"]),
                        "up_line":    float(_wd["up_line"]),
                        "lo_line":    float(_wd["lo_line"]),
                        "break_line": float(_wline),
                    }
                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 3.8 İKİLİ TEPE (M) — İki ~eşit tepe + orta vadi (boyun) aşağı kırılım (AYI)
            # ---------------------------------------------------------------
            if not pattern_found:
                _dt = _detect_double_top(sw_h_y, sw_l_y, curr_price, bar_total,
                                         is_index=_is_index_symbol(symbol))
                _dt_clean = bool(_dt) and is_clean_zone(_dt["t1_i"] - 6, _dt["t1_i"] + 6) \
                                       and is_clean_zone(_dt["t2_i"] - 6, _dt["t2_i"] + 6)
                if _dt and _dt_clean:
                    dur_months = max(1, round(_dt["dur"] / 21))
                    if _dt["state"] == "break":
                        pattern_name = f"🔻 İKİLİ TEPE (M) ({dur_months} Ay) — Aşağı Kırılım (AYI)"
                        base_score   = 72
                    else:
                        pattern_name = f"⏳ OLUŞAN İKİLİ TEPE (M) ({dur_months} Ay) — Tepe Riski %{_dt['dist']:.1f}"
                        base_score   = 55
                    desc = (f"Tepe1: {_dt['t1_v']:.2f} | Boyun (Vadi): {_dt['neck_v']:.2f} | "
                            f"Tepe2: {_dt['t2_v']:.2f} | Aşağı Hedef: {_dt['target']:.2f}")
                    chart_d = {
                        "pivot_dates":  [str(close.index[_dt['t1_i']].date()),
                                         str(close.index[_dt['neck_i']].date()),
                                         str(close.index[_dt['t2_i']].date())],
                        "pivot_prices": [_dt['t1_v'], _dt['neck_v'], _dt['t2_v']],
                        "pivot_types":  ["H", "L", "H"],
                        "neck": float(_dt['neck_v']),
                        "type": "double_top",
                    }
                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 4. YÜKSELEN ÜÇGEN — Düz direnç + yükselen destek (linregress)
            # En az 2 tepe (≤%4 fark), en az 2 dip (yükselen eğim), Max 252 bar
            # R/R >= 1.0, son pivot 60 günden yeni
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 2:
                top_v   = max(v for _, v in sw_h_y)
                flat_sh = [(i, v) for i, v in sw_h_y if abs(v - top_v) / top_v < 0.04]
                # 18 Tem: direnç temasları TAZE olmalı (ilk temas ≤160 bar) — 289 günlük bayat
                # temasın üçgeni geriye germesini önler (POLHO sahte-üçgen vakası)
                flat_sh = [(i, v) for i, v in flat_sh if bar_total - i <= 160]
                if len(flat_sh) >= 2:
                    first_sh_i = min(i for i, _ in flat_sh)
                    tri_lows   = [(i, v) for i, v in sw_l_y if i >= first_sh_i]
                    if len(tri_lows) >= 2:
                        tri_lows_s = sorted(tri_lows, key=lambda x: x[0])
                        # 18 Tem: MONOTON higher-low = yükselen üçgenin TANIMI (regresyon eğimi>0
                        # yetmiyordu; dağınık dipler POLHO'yu sahte üçgen yapıyordu). + son dip ilk
                        # dipten ≥%3 yüksek + destek R²≥0.75. Evren over-detection %5→%1.
                        _mono_ok = all(tri_lows_s[m][1] < tri_lows_s[m + 1][1] for m in range(len(tri_lows_s) - 1))
                        _rise_ok = tri_lows_s[-1][1] > tri_lows_s[0][1] * 1.03
                        x_l = np.array([i for i, _ in tri_lows_s], dtype=float)
                        y_l = np.array([v for _, v in tri_lows_s], dtype=float)
                        sl_coef = np.polyfit(x_l, y_l, 1)
                        _yp = np.polyval(sl_coef, x_l); _sst = np.sum((y_l - y_l.mean()) ** 2)
                        _r2_sup = 1 - np.sum((y_l - _yp) ** 2) / _sst if _sst > 0 else 0
                        if sl_coef[0] > 0 and _mono_ok and _rise_ok and _r2_sup >= 0.75:   # Yükselen destek (kalibre)
                            avg_res = sum(v for _, v in flat_sh) / len(flat_sh)
                            first_i = min(first_sh_i, tri_lows_s[0][0])
                            last_i  = max(max(i for i, _ in flat_sh), tri_lows_s[-1][0])
                            dur_bars = last_i - first_i
                            if 20 <= dur_bars <= 252 and (bar_total - last_i) <= 60:
                                support_now = float(np.polyval(sl_coef, bar_total - 1))
                                breaking    = curr_price >= avg_res * 0.98 and curr_price <= avg_res * 1.06
                                approaching = support_now * 0.99 <= curr_price <= avg_res * 0.98
                                if breaking or approaching:
                                    target = avg_res + (avg_res - support_now)
                                    risk   = max(curr_price - support_now * 0.98, 0.01)
                                    rr     = (target - curr_price) / risk
                                    if rr >= 1.0:
                                        dur_months = max(1, round(dur_bars / 21))
                                        p_name  = (f"📐 YÜKS. ÜÇGEN ({dur_months} Ay) — Kırılım"
                                                   if breaking else
                                                   f"⏳ OLUŞAN ÜÇGEN ({dur_months} Ay) — Dirence Yaklaşıyor")
                                        p_desc  = (f"Direnç: {avg_res:.2f} | Destek: {support_now:.2f} | "
                                                   f"Hedef: {target:.2f} | {len(flat_sh)} tepe temas")
                                        chart_d = {
                                            "type":       "triangle",
                                            "date_start": str(close.index[max(0, first_i)].date()),
                                            "resistance": float(avg_res),
                                            "pivot_dates":  ([str(close.index[i].date()) for i, _ in flat_sh] +
                                                             [str(close.index[i].date()) for i, _ in tri_lows_s]),
                                            "pivot_prices": ([v for _, v in flat_sh] +
                                                             [v for _, v in tri_lows_s]),
                                            "pivot_types":  (["H"] * len(flat_sh) +
                                                             ["L"] * len(tri_lows_s)),
                                        }
                                        _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(avg_res))
                                        chart_d["breakout_state"]     = int(_bk_st)
                                        chart_d["breakout_gap_pct"]   = float(_bk_gap)
                                        chart_d["breakout_vol_ratio"] = float(_bk_vol)
                                        pattern_found = True
                                        pattern_name  = p_name; desc = p_desc
                                        base_score    = 88 if breaking else 68
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 4.1 ALÇALAN ÜÇGEN — Düz destek + alçalan direnç (AYI)
            # Yükselen üçgenin aynası (20 Tem 2026 kalibrasyon: ARCLK ✓, evren %1).
            # Direnç katı-monoton DEĞİL — gerçek alçalan üçgende küçük sıçramalar
            # olur (ARCLK 117→121→117); aşağı eğim + son tepe ≥%3 düşük + R²≥0.70.
            # Sadece panel görünürlüğü (bu fonksiyon) — batch tarayıcılara girmez.
            # ---------------------------------------------------------------
            if not pattern_found and len(sw_h_y) >= 2 and len(sw_l_y) >= 2:
                _dtr_bot  = min(v for _, v in sw_l_y)
                _dtr_flat = [(i, v) for i, v in sw_l_y
                             if abs(v - _dtr_bot) / _dtr_bot < 0.04 and bar_total - i <= 160]
                if len(_dtr_flat) >= 2:
                    _dtr_fs   = min(i for i, _ in _dtr_flat)
                    _dtr_tops = sorted([(i, v) for i, v in sw_h_y if i >= _dtr_fs],
                                       key=lambda x: x[0])
                    if len(_dtr_tops) >= 2 and _dtr_tops[-1][1] < _dtr_tops[0][1] * 0.97:
                        _dtr_x  = np.array([i for i, _ in _dtr_tops], dtype=float)
                        _dtr_y  = np.array([v for _, v in _dtr_tops], dtype=float)
                        _dtr_cf = np.polyfit(_dtr_x, _dtr_y, 1)
                        _dtr_yp = np.polyval(_dtr_cf, _dtr_x)
                        _dtr_sst = np.sum((_dtr_y - _dtr_y.mean()) ** 2)
                        _dtr_r2  = 1 - np.sum((_dtr_y - _dtr_yp) ** 2) / _dtr_sst if _dtr_sst > 0 else 0
                        if _dtr_cf[0] < 0 and _dtr_r2 >= 0.70:
                            _dtr_sup   = sum(v for _, v in _dtr_flat) / len(_dtr_flat)
                            _dtr_first = min(_dtr_fs, _dtr_tops[0][0])
                            _dtr_last  = max(max(i for i, _ in _dtr_flat), _dtr_tops[-1][0])
                            _dtr_dur   = _dtr_last - _dtr_first
                            if 20 <= _dtr_dur <= 252 and (bar_total - _dtr_last) <= 60:
                                _dtr_res  = float(np.polyval(_dtr_cf, bar_total - 1))
                                _dtr_brk  = _dtr_sup * 0.94 <= curr_price <= _dtr_sup * 1.02
                                _dtr_appr = _dtr_sup * 1.02 < curr_price <= _dtr_res * 1.01
                                if _dtr_brk or _dtr_appr:
                                    _dtr_tgt = max(_dtr_sup - (_dtr_tops[0][1] - _dtr_sup), 0.01)
                                    dur_months = max(1, round(_dtr_dur / 21))
                                    if _dtr_brk:
                                        pattern_name = f"📐 ALÇALAN ÜÇGEN ({dur_months} Ay) — Aşağı Kırılım (AYI)"
                                        base_score   = 71
                                    else:
                                        _dtr_dist = (curr_price - _dtr_sup) / _dtr_sup * 100
                                        pattern_name = (f"⏳ OLUŞAN ALÇALAN ÜÇGEN ({dur_months} Ay) — "
                                                        f"Desteğe İniyor %{_dtr_dist:.1f}")
                                        base_score   = 55
                                    desc = (f"Destek: {_dtr_sup:.2f} | Direnç: {_dtr_res:.2f} | "
                                            f"Aşağı Hedef: {_dtr_tgt:.2f} | {len(_dtr_flat)} destek teması")
                                    chart_d = {
                                        "type":       "dtriangle",
                                        "date_start": str(close.index[max(0, _dtr_first)].date()),
                                        "support":    float(_dtr_sup),
                                        "res_now":    float(_dtr_res),
                                        "target":     float(_dtr_tgt),
                                        "pivot_dates":  ([str(close.index[i].date()) for i, _ in _dtr_tops] +
                                                         [str(close.index[i].date()) for i, _ in _dtr_flat]),
                                        "pivot_prices": ([v for _, v in _dtr_tops] +
                                                         [v for _, v in _dtr_flat]),
                                        "pivot_types":  (["H"] * len(_dtr_tops) +
                                                         ["L"] * len(_dtr_flat)),
                                    }
                                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 4.5 RANGE (YATAY BANT) — Ham fiyat bazlı
            # ---------------------------------------------------------------
            if not pattern_found:
                for rng_window in [60, 90, 120, 180]:
                    if len(df) < rng_window: continue
                    period_max  = float(high.iloc[-rng_window:].max())
                    period_min  = float(low.iloc[-rng_window:].min())
                    if period_min <= 0: continue
                    range_width = (period_max - period_min) / period_min
                    if range_width < 0.15:
                        breaking_up = curr_price >= period_max * 0.98 and curr_price <= period_max * 1.04
                        bouncing_up = curr_price >= period_min * 0.98 and curr_price <= period_min * 1.04
                        if breaking_up or bouncing_up:
                            chart_d = {
                                "type": "range",
                                "date_start": str(close.index[max(0, bar_total - rng_window)].date()),
                                "resistance": float(period_max),
                                "support":    float(period_min),
                            }
                            if breaking_up:
                                _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(period_max))
                                chart_d["breakout_state"]     = int(_bk_st)
                                chart_d["breakout_gap_pct"]   = float(_bk_gap)
                                chart_d["breakout_vol_ratio"] = float(_bk_vol)
                            pattern_found = True
                            p_name = f"🧱 RANGE DİRENCİ ({rng_window} Gün)" if breaking_up else f"🧱 RANGE DESTEĞİ ({rng_window} Gün)"
                            p_desc = (f"{rng_window} gündür süren yatay kanal direnci kırılıyor!" if breaking_up
                                      else f"{rng_window} gündür süren bandın dibinden destek aldı.")
                            pattern_name = p_name; desc = p_desc
                            base_score   = 88 if breaking_up else 85
                            break
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 4.6 ÇANAK (Saucer / Rounding Bottom)
            # ---------------------------------------------------------------
            if not pattern_found and len(df) >= 100:
                lb  = min(len(df), 120)
                seg = lb // 3
                left_part   = df.iloc[-lb:        -lb + seg]
                middle_part = df.iloc[-lb + seg:  -lb + 2*seg]
                right_part  = df.iloc[-lb + 2*seg:]
                if len(left_part) > 5 and len(middle_part) > 5 and len(right_part) > 5:
                    left_high  = float(left_part['High'].max())
                    cup_bottom = float(middle_part['Low'].min())
                    right_high = float(right_part['High'].max())
                    if ((left_high - cup_bottom) / cup_bottom > 0.12
                            and float(middle_part['Low'].mean()) < float(left_part['Low'].mean())
                            and (curr_price - cup_bottom) / cup_bottom > 0.08
                            and right_high >= left_high * 0.60
                            and curr_price >= right_high * 0.98):
                        chart_d = {
                            "type": "saucer",
                            "date_start": str(close.index[max(0, bar_total - lb)].date()),
                            "left_high":  float(left_high),
                            "cup_bottom": float(cup_bottom),
                            "right_high": float(right_high),
                        }
                        pattern_found = True
                        pattern_name  = "🥣 ÇANAK (Dipten Dönüş)"
                        base_score    = 88
                        desc = f"Sol Tepe: {left_high:.2f} | Dip: {cup_bottom:.2f} | Sağ Direnç: {right_high:.2f}"
            if pattern_found:   # 11 — aday havuzuna
                _cands.append((base_score, len(_cands), pattern_name, desc, chart_d))
                pattern_found = False; chart_d = None

            # ---------------------------------------------------------------
            # 5. QUASIMODO (QML) — Son 6 zigzag pivotu üzerinden
            # (11: yapısal formasyon adayı varsa fallback'ler çalışmaz)
            # ---------------------------------------------------------------
            if not _cands and not pattern_found and len(zz_chron) >= 4:
                recent = zz_chron[-6:]
                r_l = [(i, p) for (i, p, t) in recent if t == 'L']
                r_h = [(i, p) for (i, p, t) in recent if t == 'H']
                if len(r_l) >= 2 and len(r_h) >= 2:
                    for qi in range(len(r_l) - 1):
                        l_left_idx, l_left_p = r_l[qi]
                        mid_h = [(i, p) for (i, p) in r_h if i > l_left_idx]
                        if not mid_h: continue
                        h_mid_idx, h_mid_p = mid_h[0]
                        ll_list = [(i, p) for (i, p) in r_l if i > h_mid_idx]
                        if not ll_list: continue
                        ll_idx, ll_p = ll_list[0]
                        hh_list = [(i, p) for (i, p) in r_h if i > ll_idx]
                        if not hh_list: continue
                        hh_idx, hh_p = hh_list[0]
                        if (ll_p < l_left_p * 0.98 and hh_p > h_mid_p * 1.01
                                and curr_price >= l_left_p * 0.95 and curr_price <= l_left_p * 1.05):
                            chart_d = {
                                "type": "qml",
                                "date_start": str(close.index[max(0, l_left_idx - 3)].date()),
                                "pivot_dates":  [str(close.index[i].date()) for i in [l_left_idx, h_mid_idx, ll_idx, hh_idx]],
                                "pivot_prices": [float(l_left_p), float(h_mid_p), float(ll_p), float(hh_p)],
                                "pivot_types":  ["L", "H", "L", "H"],
                                "qml_line": float(l_left_p),
                            }
                            pattern_found = True
                            pattern_name  = "🧲 QUASIMODO (QML)"
                            base_score    = 92
                            desc = f"QML Çizgisi: {l_left_p:.2f} | Baş Dip: {ll_p:.2f} | Kırılım Tepesi: {hh_p:.2f}"
                            break

            # ---------------------------------------------------------------
            # 6. 3 DRIVE (Üç Düşen Dip) — Son 3 zigzag dibi
            # ---------------------------------------------------------------
            if not _cands and not pattern_found and len(zz_l) >= 3:
                (d1_i, d1), (d2_i, d2), (d3_i, d3) = zz_l[-3], zz_l[-2], zz_l[-1]
                if d1 > d2 > d3:
                    drop1 = d1 - d2; drop2 = d2 - d3
                    if drop1 > 0 and abs(drop1 - drop2) / drop1 < 0.25:
                        if curr_price > d3 * 1.015 and curr_price < d2:
                            chart_d = {
                                "type": "three_drive",
                                "date_start": str(close.index[max(0, d1_i - 3)].date()),
                                "pivot_dates":  [str(close.index[i].date()) for i in [d1_i, d2_i, d3_i]],
                                "pivot_prices": [float(d1), float(d2), float(d3)],
                            }
                            pattern_found = True
                            pattern_name  = "🎢 3 DRIVE (DİP)"
                            base_score    = 85
                            desc = f"Dip1: {d1:.2f} | Dip2: {d2:.2f} | Dip3: {d3:.2f} | Simetri Sapması: %{abs(drop1-drop2)/drop1*100:.0f}"

            # ---------------------------------------------------------------
            # 7. GÜÇLÜ DESTEK / DİRENÇ TESTİ
            # ---------------------------------------------------------------
            if not _cands and not pattern_found and len(df) >= 100:
                sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                for level in sorted(sr_levels, key=lambda x: abs(x - curr_price)):
                    if abs(curr_price - level) / level <= 0.015:
                        chart_d = {
                            "type": "sr_level",
                            "date_start": str(close.index[max(0, bar_total - 60)].date()),
                            "level":      float(level),
                            "is_support": curr_price >= level,
                        }
                        pattern_found = True
                        if curr_price >= level:
                            pattern_name = "🧱 GÜÇLÜ DESTEK TESTİ"
                            desc = f"Geçmişte ≥3 kez test edilen destek: {level:.2f}"
                            base_score = 85
                        else:
                            pattern_name = "⚔️ GÜÇLÜ DİRENÇ TESTİ"
                            desc = f"Geçmişte ≥3 kez reddedilen direnç: {level:.2f}"
                            base_score = 88
                        break

            # ---------------------------------------------------------------
            # 11 — EN İYİ ADAY SEÇİMİ: tüm yapısal detektörler koştu; en
            # yüksek taban puanlı formasyon kazanır (eşitlikte önce gelen blok).
            # Eskiden ilk bulunan kazanıyordu — çok-formasyonlu hissede zayıf
            # olan güçlüyü gölgeleyebiliyordu.
            # ---------------------------------------------------------------
            if _cands:
                _cands.sort(key=lambda c: (-c[0], c[1]))
                base_score, _c_ord, pattern_name, desc, chart_d = _cands[0]
                pattern_found = True

            # ---------------------------------------------------------------
            # KALİTE PUANLAMASI
            # ---------------------------------------------------------------
            if pattern_found:
                q_score = base_score
                if ("FİNCAN" in pattern_name or "TOBO" in pattern_name
                        or "QML" in pattern_name or "ÇİFT DİP" in pattern_name
                        or "DÜŞEN KAMA" in pattern_name):
                    q_score += 15
                avg_vol   = float(volume.iloc[-20:].mean())
                vol_ratio = float(volume.iloc[-1]) / avg_vol if avg_vol > 0 else 1
                if vol_ratio > 2.5:
                    q_score += 25; desc += " (🚀 Ultra Hacim)"
                elif vol_ratio > 1.5:
                    q_score += 12
                sma50 = float(close.rolling(50).mean().iloc[-1])
                if curr_price > sma50: q_score += 8
                if (float(close.iloc[-1]) < float(open_.iloc[-1])
                        and float(close.iloc[-2]) < float(open_.iloc[-2])):
                    q_score -= 35; desc += " (⚠️ Düşüşte)"
                if avg_vol < 5000000:
                    pattern_name += " (⚠️ SIĞ TAHTA)"
                    desc += " | 🚨 Dikkat: Ortalama işlem hacmi 5 Milyon lotun altında."
                if not np.isnan(sma200) and curr_price < sma200:
                    pattern_name += " (⚠️ SMA200 Altında)"
                    desc += " | 📉 Risk Uyarısı: Fiyat 200 günlük ana ortalamanın altında."
                    q_score -= 10
                if mansfield_val > 0:   q_score += 10
                elif mansfield_val < 0: q_score -= 10
                return {
                    "Sembol":    symbol,
                    "Fiyat":     curr_price,
                    "Formasyon": pattern_name,
                    "Detay":     desc,
                    "Skor":      int(q_score),
                    "Hacim":     float(volume.iloc[-1]),
                    "ChartData": chart_d,
                }

        except Exception:
            return None
        return None

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_pattern, sym) for sym in asset_list]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results:
        return pd.DataFrame(results).sort_values(by=["Skor", "Hacim"], ascending=[False, False])
    return pd.DataFrame()

@st.cache_data(ttl=900)
def scan_golden_pattern_agent(asset_list, category="S&P 500"):
    """
    💎 Altın Set-up & VIP Formasyon Ajanı (Mesafe Kontrollü)
    1. AŞAMA: Orijinal Altın Set-up kriterlerini arar (Güç, Ucuzluk, Enerji).
    2. AŞAMA: Sadece bu kriterleri geçenlerde formasyon ve "kırılıma kalan mesafe" hesaplaması yapar.
    Formasyon bulunamazsa → Hazırlık Listesi (Baz Kurulumu veya Beklemede).
    """
    data = get_batch_data_cached(asset_list, period="1y")

    if data.empty:
        return {"formations": pd.DataFrame(), "hazirlik": pd.DataFrame()}

    bench = get_benchmark_data(category)
    results = []
    hazirlik_list = []
    
    for symbol in asset_list:
        try:
            # Sütun yapısını kontrol et (MultiIndex vs Tekli)
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: 
                    continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()
            
            # Yeterli veri var mı?
            if len(df) < 150: 
                continue
            
            # Temel verileri al
            close = df['Close']
            high = df['High']
            low = df['Low']
            volume = df['Volume']
            open_ = df['Open']
            
            curr_price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2])
            
            # Hacim kontrolü (Sığ tahtaları ele)
            avg_vol = volume.iloc[-20:].mean()
            if avg_vol < 1000000: 
                continue 
            
            last_vol = float(volume.iloc[-1])
            
            # =========================================================
            # 🚀 1. AŞAMA: ALTIN FIRSAT KRİTERLERİ
            # (get_golden_trio_batch_scan ile birebir aynı mantık)
            # =========================================================

            # RSI hesabı (Royal Flush Nadir Set-up + enerji için gerekli)
            delta   = close.diff()
            gain    = delta.clip(lower=0).rolling(window=14).mean()
            loss    = -delta.clip(upper=0).rolling(window=14).mean()
            rsi_s   = 100 - (100 / (1 + gain / loss))
            last_rsi = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0

            # KRİTER 1 — Son 10 günde endeksten güçlü
            is_powerful = False
            if bench is not None and len(bench) > 10 and len(close) > 10:
                try:
                    stock_ret = (curr_price / float(close.iloc[-10])) - 1
                    index_ret = (float(bench.iloc[-1]) / float(bench.iloc[-10])) - 1
                    is_powerful = stock_ret > index_ret
                except Exception:
                    is_powerful = last_rsi > 45   # fallback
            else:
                is_powerful = last_rsi > 45

            # KRİTER 2 — Son 60 güne göre ucuz (bandın alt %65'i — ICT Discount zone ile uyumlu)
            high_60 = high.iloc[-60:].max()
            low_60  = low.iloc[-60:].min()
            rng_60  = high_60 - low_60
            is_discount = (rng_60 > 0) and ((curr_price - low_60) / rng_60 < 0.65)

            # KRİTER 3 — Hacim/Enerji artıyor
            is_energy = (last_vol > avg_vol * 1.05) or (last_rsi > 45)

            # Mansfield RS (görüntüleme için, filtre değil)
            mansfield_gp = 0.0
            if bench is not None and len(close) > 60:
                try:
                    common_i = close.index.intersection(bench.index)
                    if len(common_i) > 55:
                        rs_r = close.reindex(common_i) / bench.reindex(common_i)
                        rs_m = rs_r.rolling(50).mean()
                        m_s  = ((rs_r / rs_m) - 1) * 10
                        mansfield_gp = float(m_s.iloc[-1]) if not np.isnan(m_s.iloc[-1]) else 0.0
                except Exception:
                    pass

            # Altın Set-up değilse geç
            if not (is_powerful and is_discount and is_energy):
                continue
                
            # =========================================================
            # 🚀 2. AŞAMA: FORMASYON VE MESAFE (CEZA) ARAMASI
            # =========================================================
            
            body_top = df[['Open', 'Close']].max(axis=1)
            body_bottom = df[['Open', 'Close']].min(axis=1)
            
            vol_ratio = last_vol / avg_vol if avg_vol > 0 else 1
            
            warnings = []
            if vol_ratio < 1.1: 
                warnings.append("Hacim Cılız")
            
            pct_change = (curr_price - prev_close) / prev_close
            if pct_change <= -0.01: 
                warnings.append("Düşüşte")
            
            body_size = curr_price - open_.iloc[-1]
            is_strong_candle = body_size > 0 and curr_price > (high.iloc[-1] + low.iloc[-1]) / 2
            if not is_strong_candle and pct_change > -0.01: 
                warnings.append("Kararsız Mum")
            
            # Hareketli Ortalama Kontrolleri
            sma50 = close.rolling(50).mean().iloc[-1]
            sma200 = close.rolling(200).mean().iloc[-1]
            if curr_price < sma50:
                warnings.append("SMA50 Altında")
            if curr_price < sma200:
                warnings.append("SMA200 Altında")

            warning_text = f" (⚠️ {', '.join(warnings)})" if warnings else " (✅ Kusursuz)"

            # ── Platin tespiti — VIP Formasyon listesinde ♠️ ikonu için (SMA200+SMA50+RSI<70)
            is_platin = (
                curr_price > sma200 and   # Uzun vade trend yukarı
                curr_price > sma50  and   # Kısa vade yapı sağlam
                last_rsi < 70             # Aşırı ısınmamış
            )

            pattern_found = False
            p_name = ""
            base_score = 0

            # (4 Tem 2026) Ölü zigzag silindi — VIP ajanında QML/3Drive yok,
            # zz_chron hiçbir yerde kullanılmıyordu.

            # A) FİNCAN KULP — Swing tabanlı + polinom U-şekil doğrulaması
            def _find_swings_gp(series, lookback=8):
                highs, lows = [], []
                arr = series.values.astype(float)
                n   = len(arr)
                for i in range(lookback, n - lookback):
                    w = arr[i - lookback: i + lookback + 1]
                    if arr[i] >= w.max() - 1e-9: highs.append((i, arr[i]))
                    if arr[i] <= w.min() + 1e-9: lows.append((i, arr[i]))
                return highs, lows

            _bt    = len(close)
            close_np = close.values.astype(float)
            vol_np   = volume.values.astype(float)
            # 7 — Pivotlar fitil uçlarından; 10 — uyarlanabilir eşikle budama
            # (4 Tem 2026 — scan_chart_patterns ile senkron)
            _swh, _ = _find_swings_gp(high, lookback=8)
            _, _swl = _find_swings_gp(low, lookback=8)
            _adapt_thr = pattern_core.adaptive_threshold(close_np)
            _swh, _swl = pattern_core.prune_pivots(_swh, _swl, _adapt_thr)
            _swh_y = [(i, v) for i, v in _swh if i >= _bt - 252]
            _swl_y = [(i, v) for i, v in _swl if i >= _bt - 252]

            # 11 — aday havuzu: tüm detektörler koşar, en yüksek puanlı kazanır
            _gp_cands = []

            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 1:
                for ri in range(len(_swh_y) - 1, 0, -1):
                    if pattern_found: break
                    sh2_i, sh2_v = _swh_y[ri]
                    # 18 Tem: sağ-rim tazelik kapısı KALDIRILDI (batch=actionable-only; extended/failed
                    # zaten handle_dur+R/R'de doğal olarak düşer, tarayıcı temiz kalır).
                    for li in range(ri - 1, max(ri - 12, -1), -1):
                        sh1_i, sh1_v = _swh_y[li]
                        cup_dur = sh2_i - sh1_i
                        if not (40 <= cup_dur <= 252): continue
                        # 6 — Ön-trend: kupa öncesi yükseliş şartı
                        if not pattern_core.cup_pretrend_ok(close_np, sh1_i): continue
                        cup_lows = [(i, v) for i, v in _swl_y if sh1_i < i < sh2_i]
                        if not cup_lows: continue
                        sl_i, sl_v = min(cup_lows, key=lambda x: x[1])
                        depth = (sh1_v - sl_v) / sh1_v
                        if not (0.12 <= depth <= 0.55): continue
                        # Rim hizalaması scan_chart_patterns ile senkron (18 Tem: PC['cup_rim']=0.085)
                        if abs(sh1_v - sh2_v) / sh1_v > pattern_core.PC['cup_rim']: continue
                        try:
                            cup_arr = close.iloc[sh1_i:sh2_i + 1].values.astype(float)
                            if len(cup_arr) < 10: continue
                            # 16 Haz 2026 — Polinom fit 5g EMA üzerinde (smoothing fix).
                            # scan_chart_patterns ile aynı kalıp — TOASO örneği ders.
                            cup_smooth = pd.Series(cup_arr).ewm(span=5, adjust=False).mean().values
                            xf = np.linspace(0, 1, len(cup_arr))
                            cf = np.polyfit(xf, cup_smooth, 2)
                            yp = np.polyval(cf, xf)
                            ss_res = np.sum((cup_smooth - yp) ** 2)
                            ss_tot = np.sum((cup_smooth - cup_smooth.mean()) ** 2)
                            r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                            if cf[0] <= 0: continue
                        except: continue
                        # FIX (30 May 2026): Şekil doğrulaması — iki fonksiyon ortak helper (R²≥0.78 dahil)
                        if not _validate_cup_shape(cup_arr, sh1_i, sl_i, sh2_i, r2): continue
                        # Wick/Body filtresi: fincan bölgesi gürültülü değil mi?
                        o_z = open_.iloc[sh1_i:sh2_i+1].values.astype(float)
                        c_z = close.iloc[sh1_i:sh2_i+1].values.astype(float)
                        h_z = high.iloc[sh1_i:sh2_i+1].values.astype(float)
                        l_z = low.iloc[sh1_i:sh2_i+1].values.astype(float)
                        _bodies = np.abs(c_z - o_z); _wicks = (h_z - l_z) - _bodies
                        if np.median(_bodies) > 1e-9 and np.median(_wicks) > 2.0 * np.median(_bodies): continue
                        h_lows = [(i, v) for i, v in _swl_y if i > sh2_i]
                        if h_lows:
                            hl_i, hl_v = h_lows[0]
                        else:
                            after = close.iloc[sh2_i:]
                            if len(after) < 3: continue
                            rel = int(after.values.argmin())
                            hl_i, hl_v = sh2_i + rel, float(after.iloc[rel])
                        if not (hl_v > sl_v + (sh2_v - sl_v) * 0.35): continue
                        if not (hl_v > sh2_v * pattern_core.PC['cup_handle_lo']): continue  # senkron (0.82→0.81)
                        # 8 — Kulp süresi: kupaya oranla kısa olmalı
                        if not pattern_core.handle_dur_ok(sh2_i, _bt, cup_dur): continue
                        target = sh2_v + (sh2_v - sl_v)
                        risk   = max(curr_price - hl_v * 0.98, 0.01)
                        if (target - curr_price) / risk < 1.0: continue
                        # 12 — retest / sahte kırılım katmanı
                        _pb = pattern_core.detect_post_breakout(close_np, vol_np, sh2_v)
                        if _pb['failed']: continue
                        dist = ((sh2_v - curr_price) / sh2_v * 100) if curr_price < sh2_v else 0
                        retesting = _pb['retest']
                        breaking  = (not retesting) and sh2_v * 0.97 <= curr_price <= sh2_v * 1.10
                        # 3 — OLUŞAN için boyuna max %12 uzaklık (18 Tem: curr<neck*0.97 guard —
                        # yoksa neck üstünde dist=0 sahte-form; extended/failed batch'te gösterilmez)
                        forming   = (not retesting) and (not breaking) and curr_price < sh2_v * 0.97 \
                                    and curr_price >= hl_v * 0.98 and dist <= pattern_core.PC['form_max_dist']
                        if not (retesting or breaking or forming): continue
                        dur_months = max(1, round(cup_dur / 21))
                        if retesting:
                            p_name = f"🎯 FİNCAN KULP RETEST ({dur_months} Ay) — Boyun Destek Testi"
                            base_score = 94
                        elif breaking:
                            p_name = f"☕ FİNCAN KULP ({dur_months} Ay) — Kırılım Bölgesinde"
                            base_score = 92
                        else:
                            p_name = f"⏳ OLUŞAN FİNCAN KULP ({dur_months} Ay) — %{dist:.1f} kaldı"
                            base_score = 75
                        # 9 — hacim imzası + kırılım hacmi bonusu
                        _vs = pattern_core.volume_signature(vol_np, sh1_i, sh2_i, min(hl_i, _bt - 1))
                        _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(sh2_v))
                        base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                        pattern_found = True
                        break
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # B) TOBO — Swing tabanlı: 5 pivot L, H, L(derin), H, L
            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 3:
                for i_rs in range(len(_swl_y) - 1, 1, -1):
                    if pattern_found: break
                    sl3_i, sl3_v = _swl_y[i_rs]
                    if _bt - sl3_i > 60: continue
                    for i_hd in range(i_rs - 1, 0, -1):
                        if pattern_found: break
                        sl2_i, sl2_v = _swl_y[i_hd]
                        for i_ls in range(i_hd - 1, max(i_hd - 8, -1), -1):
                            sl1_i, sl1_v = _swl_y[i_ls]
                            dur = sl3_i - sl1_i
                            if not (40 <= dur <= 252): continue
                            if not (sl2_v < sl1_v * 0.95 and sl2_v < sl3_v * 0.95): continue
                            # 6 — Ön-trend: TOBO öncesi düşüş şartı
                            if not pattern_core.tobo_pretrend_ok(close_np, sl1_i, sl2_v): continue
                            sh1_c = [(i, v) for i, v in _swh_y if sl1_i < i < sl2_i]
                            sh2_c = [(i, v) for i, v in _swh_y if sl2_i < i < sl3_i]
                            if not sh1_c or not sh2_c: continue
                            sh1_i, sh1_v = max(sh1_c, key=lambda x: x[1])
                            sh2_i, sh2_v = max(sh2_c, key=lambda x: x[1])
                            # FIX (30 May 2026): TOBO şekil doğrulaması (ortak helper)
                            _tok, neck = _validate_tobo_shape(
                                sl1_i, sl1_v, sl2_i, sl2_v, sl3_i, sl3_v,
                                sh1_i, sh1_v, sh2_i, sh2_v, _bt)
                            if not _tok: continue
                            if abs(sl1_v - sl3_v) / sl1_v > 0.15: continue
                            recovery = (sl3_v - sl2_v) / (neck - sl2_v) if (neck - sl2_v) > 0 else 0
                            if recovery < 0.45: continue
                            # Wick/Body filtresi: TOBO bölgesi gürültülü değil mi?
                            o_z = open_.iloc[sl1_i:sl3_i+1].values.astype(float)
                            c_z = close.iloc[sl1_i:sl3_i+1].values.astype(float)
                            h_z = high.iloc[sl1_i:sl3_i+1].values.astype(float)
                            l_z = low.iloc[sl1_i:sl3_i+1].values.astype(float)
                            _bodies = np.abs(c_z - o_z); _wicks = (h_z - l_z) - _bodies
                            if np.median(_bodies) > 1e-9 and np.median(_wicks) > 2.0 * np.median(_bodies): continue
                            target = neck + (neck - sl2_v)
                            risk   = max(curr_price - sl3_v * 0.98, 0.01)
                            if (target - curr_price) / risk < 1.0: continue
                            # 12 — retest / sahte kırılım katmanı
                            _pb = pattern_core.detect_post_breakout(close_np, vol_np, neck)
                            if _pb['failed']: continue
                            dist = ((neck - curr_price) / neck * 100) if curr_price < neck else 0
                            retesting = _pb['retest']
                            breaking  = (not retesting) and neck * 0.97 <= curr_price <= neck * 1.08
                            # 3 — OLUŞAN için boyuna max %12 uzaklık
                            forming   = (not retesting) and (not breaking) and curr_price > sl3_v * 1.01 \
                                        and curr_price < neck * 0.96 \
                                        and dist <= pattern_core.PC['form_max_dist']
                            if not (retesting or breaking or forming): continue
                            dur_months = max(1, round(dur / 21))
                            if retesting:
                                p_name = f"🎯 TOBO RETEST ({dur_months} Ay) — Boyun Destek Testi"
                                base_score = 94
                            elif breaking:
                                p_name = f"🧛 TOBO ({dur_months} Ay) — Kırılım Bölgesinde"
                                base_score = 90
                            else:
                                p_name = f"⏳ OLUŞAN TOBO ({dur_months} Ay) — %{dist:.1f} kaldı"
                                base_score = 72
                            # 9 — hacim imzası + kırılım hacmi bonusu
                            _vs = pattern_core.volume_signature(vol_np, sl1_i, sl3_i, sl3_i)
                            _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(neck))
                            base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                            pattern_found = True
                            break
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # B.5) ÇİFT DİP (W) — İki ~eşit dip + orta tepe (boyun) kırılımı
            if not pattern_found:
                _db = _detect_double_bottom(_swl_y, _swh_y, curr_price, _bt,
                                            is_index=_is_index_symbol(symbol))
                if _db:
                    # Wick/Body filtresi SADECE iki dip cevresinde (±6 bar) —
                    # uzun W span'inde tum bar filtresi gercek W'leri eler.
                    def _zone_clean(ci):
                        _s = max(0, ci - 6); _e = min(ci + 7, _bt)
                        if _e - _s < 5: return True
                        o_z = open_.iloc[_s:_e].values.astype(float)
                        c_z = close.iloc[_s:_e].values.astype(float)
                        h_z = high.iloc[_s:_e].values.astype(float)
                        l_z = low.iloc[_s:_e].values.astype(float)
                        _bd = np.abs(c_z - o_z); _wk = (h_z - l_z) - _bd
                        return not (np.median(_bd) > 1e-9 and np.median(_wk) > 2.0 * np.median(_bd))
                    if _zone_clean(_db["d1_i"]) and _zone_clean(_db["d2_i"]):
                        # 12 — retest / sahte kırılım katmanı
                        _pb = pattern_core.detect_post_breakout(close_np, vol_np, _db['neck_v'])
                        if not _pb['failed']:
                            dur_months = max(1, round(_db["dur"] / 21))
                            if _pb['retest']:
                                p_name = f"🎯 ÇİFT DİP (W) RETEST ({dur_months} Ay) — Boyun Destek Testi"
                                base_score = 94
                            elif _db["state"] == "break":
                                p_name = f"🔷 ÇİFT DİP (W) ({dur_months} Ay) — Kırılım Bölgesinde"
                                base_score = 90
                            else:
                                p_name = f"⏳ OLUŞAN ÇİFT DİP (W) ({dur_months} Ay) — %{_db['dist']:.1f} kaldı"
                                base_score = 72
                            # 9 — hacim imzası + kırılım hacmi bonusu
                            _vs = pattern_core.volume_signature(vol_np, _db['d1_i'], _db['d2_i'], _db['d2_i'])
                            _bk_st, _bk_gap, _bk_vol = _detect_breakout_state(df, float(_db['neck_v']))
                            base_score += _vs['bonus'] + pattern_core.breakout_bonus(_bk_st, _bk_vol)
                            pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # B.7) KAMA (Düşen=boğa / Yükselen=ayı) — yakınsayan trend çizgileri
            if not pattern_found:
                _wd = _detect_wedge(_swh_y, _swl_y, close, high, low, volume,
                                    curr_price, _bt)
                if _wd:
                    dur_months = max(1, round(_wd["dur"] / 21))
                    if _wd["kind"] == "falling":
                        if _wd["state"] == "break":
                            p_name = f"📉 DÜŞEN KAMA ({dur_months} Ay) — Yukarı Kırılım"
                            base_score = 90
                        else:
                            p_name = f"⏳ OLUŞAN DÜŞEN KAMA ({dur_months} Ay) — %{_wd['dist']:.1f} kaldı"
                            base_score = 74
                    else:  # rising — ayı (golden set-up boğa odaklı; düşük puan)
                        if _wd["state"] == "break":
                            p_name = f"📈 YÜKSELEN KAMA ({dur_months} Ay) — Aşağı Kırılım (AYI)"
                            base_score = 50
                        else:
                            p_name = f"⏳ OLUŞAN YÜKSELEN KAMA ({dur_months} Ay) — Tepe Riski"
                            base_score = 45
                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # B.8) İKİLİ TEPE (M) — iki ~eşit tepe + orta vadi aşağı kırılım (AYI)
            if not pattern_found:
                _dt = _detect_double_top(_swh_y, _swl_y, curr_price, _bt,
                                         is_index=_is_index_symbol(symbol))
                if _dt:
                    dur_months = max(1, round(_dt["dur"] / 21))
                    if _dt["state"] == "break":
                        p_name = f"🔻 İKİLİ TEPE (M) ({dur_months} Ay) — Aşağı Kırılım (AYI)"
                        base_score = 50
                    else:
                        p_name = f"⏳ OLUŞAN İKİLİ TEPE (M) ({dur_months} Ay) — Tepe Riski"
                        base_score = 45
                    pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # C) YÜKSELEN ÜÇGEN — Düz direnç + yükselen destek
            if not pattern_found and len(_swh_y) >= 2 and len(_swl_y) >= 2:
                top_v_gp   = max(v for _, v in _swh_y)
                flat_sh_gp = [(i, v) for i, v in _swh_y if abs(v - top_v_gp) / top_v_gp < 0.04]
                # 18 Tem: scan_chart_patterns ile senkron — taze direnç + monoton higher-low + R²
                flat_sh_gp = [(i, v) for i, v in flat_sh_gp if _bt - i <= 160]
                if len(flat_sh_gp) >= 2:
                    first_sh_i_gp = min(i for i, _ in flat_sh_gp)
                    tri_lows_gp   = [(i, v) for i, v in _swl_y if i >= first_sh_i_gp]
                    if len(tri_lows_gp) >= 2:
                        tri_lows_s_gp = sorted(tri_lows_gp, key=lambda x: x[0])
                        _mono_gp = all(tri_lows_s_gp[m][1] < tri_lows_s_gp[m + 1][1] for m in range(len(tri_lows_s_gp) - 1))
                        _rise_gp = tri_lows_s_gp[-1][1] > tri_lows_s_gp[0][1] * 1.03
                        x_l_gp = np.array([i for i, _ in tri_lows_s_gp], dtype=float)
                        y_l_gp = np.array([v for _, v in tri_lows_s_gp], dtype=float)
                        sl_coef_gp = np.polyfit(x_l_gp, y_l_gp, 1)
                        _yp_gp = np.polyval(sl_coef_gp, x_l_gp); _sst_gp = np.sum((y_l_gp - y_l_gp.mean()) ** 2)
                        _r2_gp = 1 - np.sum((y_l_gp - _yp_gp) ** 2) / _sst_gp if _sst_gp > 0 else 0
                        if sl_coef_gp[0] > 0 and _mono_gp and _rise_gp and _r2_gp >= 0.75:
                            avg_res_gp  = sum(v for _, v in flat_sh_gp) / len(flat_sh_gp)
                            first_i_gp  = min(first_sh_i_gp, tri_lows_s_gp[0][0])
                            last_i_gp   = max(max(i for i, _ in flat_sh_gp), tri_lows_s_gp[-1][0])
                            dur_bars_gp = last_i_gp - first_i_gp
                            if 20 <= dur_bars_gp <= 252 and (_bt - last_i_gp) <= 60:
                                sup_gp   = float(np.polyval(sl_coef_gp, _bt - 1))
                                breaking = curr_price >= avg_res_gp * 0.98 and curr_price <= avg_res_gp * 1.06
                                approach = sup_gp * 0.99 <= curr_price <= avg_res_gp * 0.98
                                if breaking or approach:
                                    target_gp = avg_res_gp + (avg_res_gp - sup_gp)
                                    risk_gp   = max(curr_price - sup_gp * 0.98, 0.01)
                                    if (target_gp - curr_price) / risk_gp >= 1.0:
                                        dur_months_gp = max(1, round(dur_bars_gp / 21))
                                        p_name = (f"📐 YÜKS. ÜÇGEN ({dur_months_gp} Ay) — Kırılım"
                                                  if breaking else
                                                  f"⏳ OLUŞAN ÜÇGEN ({dur_months_gp} Ay) — Dirence Yaklaşıyor")
                                        base_score    = 88 if breaking else 68
                                        pattern_found = True
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # D) RANGE (YATAY BANT) — direnç kırılımı veya tabandan destek
            if not pattern_found:
                for rng_window in [60, 90, 120, 180]:
                    if len(df) < rng_window: continue
                    period_max  = float(high.iloc[-rng_window:].max())
                    period_min  = float(low.iloc[-rng_window:].min())
                    if period_min <= 0: continue
                    range_width = (period_max - period_min) / period_min
                    if range_width < 0.15:
                        breaking_up = curr_price >= period_max * 0.98 and curr_price <= period_max * 1.04
                        bouncing_up = curr_price >= period_min * 0.98 and curr_price <= period_min * 1.04
                        if breaking_up or bouncing_up:
                            p_name = (f"🧱 RANGE DİRENCİ ({rng_window} Gün)"
                                      if breaking_up else
                                      f"🧱 RANGE DESTEĞİ ({rng_window} Gün)")
                            base_score    = 88 if breaking_up else 85
                            pattern_found = True
                            break
            if pattern_found:   # 11 — aday havuzuna
                _gp_cands.append((base_score, len(_gp_cands), p_name))
                pattern_found = False

            # E) GÜÇLÜ DESTEK / DİRENÇ TESTİ
            # (11: yapısal formasyon adayı varsa fallback çalışmaz)
            if not _gp_cands and not pattern_found and len(df) >= 100:
                try:
                    sr_levels = find_smart_sr_levels(df, window=5, cluster_tolerance=0.015, min_touches=3)
                    for level in sorted(sr_levels, key=lambda x: abs(x - curr_price)):
                        if abs(curr_price - level) / level <= 0.015:
                            is_sup = curr_price >= level
                            p_name = (f"🟢 DESTEK TESTİ ({level:.2f})"
                                      if is_sup else
                                      f"🔴 DİRENÇ TESTİ ({level:.2f})")
                            base_score    = 82 if is_sup else 78
                            pattern_found = True
                            break
                except Exception:
                    pass

            # ── VIP FORMASYON = v2 MOTOR (10 Ağu 2026) — yukarıdaki v1 tespiti YOK SAYILIR ──
            # Kullanıcı kararı: VIP de v2'ye geçsin. v1 cup/tobo/üçgen/kama bloğu (yukarıda)
            # koşuyor ama sonuç buradan v2 ile eziliyor → tek doğru formasyon kaynağı = v2.
            # (Verimlilik için v1 bloğu ileride budanabilir; şimdilik güvenli-ölü.)
            pattern_found = False; p_name = ""; base_score = 0
            try:
                import formasyon_v2 as _fv2vip
                _vrep = _fv2vip.analyze_formations(df, ticker=symbol, timeframe="1d")
                _vc = _vrep.patterns[0] if (_vrep and getattr(_vrep, "patterns", None)) else None
            except Exception:
                _vc = None
            if _vc is not None:
                pattern_found = True
                _vlabel = _V2_VIP_LABEL.get(_vc.pattern, str(_vc.pattern).replace('_', ' ').title())
                _vdist = abs(float(_vc.metrics.get('distance_to_trigger_pct', 0.0)))
                if _vc.stage == 'KIRILIM_DOĞRULANDI':
                    p_name, base_score = f"{_vlabel} — Kırılım Doğrulandı", min(95.0, float(_vc.quality_score))
                elif _vc.stage in ('KIRILIM_ADAYI', 'YENİDEN_TEST'):
                    p_name, base_score = f"{_vlabel} — Kırılım Bölgesinde", min(92.0, float(_vc.quality_score))
                elif _vc.stage == 'YAKIN':
                    p_name, base_score = f"⏳ {_vlabel} — %{_vdist:.1f} kaldı", min(80.0, float(_vc.quality_score))
                else:  # OLUŞUYOR / KULP_BEKLENİYOR
                    p_name, base_score = f"⏳ Oluşan {_vlabel} — %{_vdist:.1f} kaldı", min(75.0, float(_vc.quality_score))

            # --- 3. LİSTEYE ALMA VE PUANLAMA ---
            if pattern_found:
                # Hacim çarpanı ekle
                base_score += (vol_ratio * 5)

                # Mansfield bonusu/cezası
                if mansfield_gp > 0: base_score += 8
                elif mansfield_gp < -1: base_score -= 8

                # Ceza puanlarını uygula
                if "Hacim Cılız" in warning_text: base_score -= 10
                if "Düşüşte" in warning_text: base_score -= 15
                if "SMA200 Altında" in warning_text: base_score -= 8
                if "Kararsız Mum" in warning_text: base_score -= 5

                # ── VIP FORMASYON GÜÇ FİLTRESİ (19 Haz 2026 — iki-rejim backtest kanıtlı) ──
                # Mayıs(boğa)+Haziran(ayı) 711 sinyal: RSI 55-70 (May %67/Haz %52) ve
                # 52H zirveye yakın (May %72/Haz %58) kazandırır; RSI<40 / dipte (Haz %31)
                # batırır. HER İKİ rejimde geçerli → güçlüyü öne sırala.
                try:
                    _seg_v = df.tail(252)
                    _hv, _lv = float(_seg_v['High'].max()), float(_seg_v['Low'].min())
                    _p52_v = (curr_price - _lv) / (_hv - _lv) * 100 if _hv > _lv else None
                except Exception:
                    _p52_v = None
                if _p52_v is not None and _p52_v >= 50 and last_rsi >= 55:
                    _guc_v, _guc_rank_v = '🟢 GÜÇLÜ', 0
                elif _p52_v is not None and (_p52_v < 40 or last_rsi < 45):
                    _guc_v, _guc_rank_v = '🔴 ZAYIF', 2
                else:
                    _guc_v, _guc_rank_v = '🟡 ORTA', 1
                results.append({
                    "Sembol":    symbol,
                    "Puan":      int(min(max(base_score, 10), 100)),
                    "RSI":       round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Detay":     p_name + warning_text,
                    "Guc":       _guc_v,
                    "Guc_52h":   round(_p52_v, 1) if _p52_v is not None else None,
                    "_guc_rank": _guc_rank_v,
                    "is_nadir":  is_platin,
                })
            else:
                # Formasyon yok → Hazırlık Listesi
                _sma20_h = close.rolling(20).mean()
                _std20_h = close.rolling(20).std()
                _bb_w = ((_sma20_h + 2*_std20_h) - (_sma20_h - 2*_std20_h)) / (_sma20_h + 0.0001)
                _pct30 = _bb_w.rolling(60).quantile(0.30).iloc[-1]
                is_baz = (not pd.isna(_pct30)) and (_bb_w.iloc[-1] < _pct30 * 1.1)
                etiket = "📦 Baz Kurulumu" if is_baz else "⏳ Hazırlık"
                hazirlik_list.append({
                    "Sembol":    symbol,
                    "RSI":       round(float(last_rsi), 1),
                    "Mansfield": round(mansfield_gp, 1),
                    "Hacim_Kat": round(vol_ratio, 1),
                    "Durum":     etiket,
                    "is_nadir":  is_platin,
                })

        except Exception as e:
            # Hata durumunda (örneğin veri eksikliği) o sembolü atla
            continue

    formations_df = (pd.DataFrame(results)
                       .sort_values(by=["is_nadir", "_guc_rank", "Puan"], ascending=[False, True, False])
                       .drop(columns=["_guc_rank"])
                       .reset_index(drop=True)) if results else pd.DataFrame()  # is_nadir sütunu is_platin değerini taşır
    hazirlik_df   = (pd.DataFrame(hazirlik_list)
                       .sort_values(by=["is_nadir", "Mansfield"], ascending=[False, False])
                       .reset_index(drop=True)) if hazirlik_list else pd.DataFrame()
    return {"formations": formations_df, "hazirlik": hazirlik_df}

@st.cache_data(ttl=900)
def scan_hidden_accumulation(asset_list):
    # 1. Önce Hisse Verilerini Çek
    data = get_batch_data_cached(asset_list, period="1y") # RS için süreyi 1y yaptım (önce 1mo idi)
    if data.empty: return pd.DataFrame()

    # 2. Endeks Verisini Çek (Sadece tek sefer)
    current_cat = st.session_state.get('category', 'S&P 500')
    benchmark = get_benchmark_data(current_cat)

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    # 3. Paralel İşlem (Benchmark'ı da gönderiyoruz)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # benchmark serisini her fonksiyona argüman olarak geçiyoruz
        futures = [executor.submit(process_single_accumulation, sym, df, benchmark) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    if results:
        df_res = pd.DataFrame(results)
        # SIRALAMA — SKOR ÖNCE (15 Tem 2026, ölçümle düzeltildi)
        # Eski sıra: Pocket_Pivot → Kalite → Skor → Hacim. Panel bu sıradan ilk 5'i
        # aldığı için vitrini ilk iki kriter belirliyordu. 187 likit hisse × 4 yıl
        # (15.773 sinyal) gün-nötr alfa ölçümü:
        #   Pocket_Pivot=True  → -0.71%  (False -0.05%) → 1. sıradaydı ve EN KÖTÜSÜ
        #   Kalite A vs B      → -0.16% vs -0.08%       → ayrım YOK (A öne geçiyordu
        #                                                  sadece harf sırası yüzünden)
        #   Skor >= 55         → +1.23%  (Skor < 35 → -0.76%) → tek çalışan ölçüt,
        #                                                        3. sıradaydı = etkisiz
        # Taramanın toplam alfası 0.00 çünkü kazanan yarı kaybeden yarıyla karışıyordu.
        # Skor'a göre sıralayınca ilk 5 yüksek-skor bölgesinden gelir.
        # Pocket_Pivot + Kalite kolon olarak DURUYOR (panelde gösteriliyor), yalnızca
        # sıralama anahtarı olmaktan çıkarıldı. Ölçüm detayı:
        # memory/project-donus-gunu-calismasi.md
        return df_res.sort_values(by=["Skor", "Hacim"], ascending=[False, False])

    return pd.DataFrame()

@st.cache_data(ttl=3600)
def analyze_market_intelligence(asset_list, category="S&P 500"):
    # period="1y" — Master Scan preload ile cache key uyumu (mismatch fix)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    bench = get_benchmark_data(category)

    signals = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_radar1, sym, df, bench) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: signals.append(res)

    return pd.DataFrame(signals).sort_values(by="Skor", ascending=False) if signals else pd.DataFrame()

@st.cache_data(ttl=900)
def radar2_scan(asset_list, min_price=5, max_price=5000, min_avg_vol_m=0.5):
    # Akıllı önbellek + ban korumalı veri çekimi
    try:
        data = get_batch_data_cached(asset_list, period="1y")
    except Exception as e:
        return pd.DataFrame()

    if data.empty: return pd.DataFrame()

    # Kategori bazlı doğru benchmark (BIST → XU100, diğerleri → S&P500)
    cat = st.session_state.get('category', 'S&P 500')
    bench_ticker = "XU100.IS" if "BIST" in cat else "^GSPC"
    try:
        idx_df = get_safe_historical_data(bench_ticker, period="1y")
        idx = idx_df['Close'] if idx_df is not None and not idx_df.empty else None
    except:
        idx = None

    results = []
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]: stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_radar2, sym, df, idx, min_price, max_price, min_avg_vol_m) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)

    return pd.DataFrame(results).sort_values(by=["Skor", "RS"], ascending=False).head(50) if results else pd.DataFrame()

def scan_stp_uyanis_batch(asset_list):
    """STP Uyanış gözlem havuzu — skora/AI'a girmeyen, yalnız izleme listesi.

    Master Scan'in zaten belleğe aldığı 1 yıllık OHLCV fotoğrafını kullanır; ek
    veri isteği yapmaz. Uzun grup 15 kesintisiz seans veya 15 seans içindeki
    tek kısa tepkiyi tolere eder; T+2 teyidi sonrası T+5 planını izler.
    7–14 kesintisiz seanslık grup yalnız erken gözlemdir; skor/AI'a girmez ve
    işlem planı oluşturmaz.
    """
    try:
        data = get_batch_data_cached(asset_list, period="1y")
    except Exception:
        return pd.DataFrame()
    if data is None or data.empty:
        return pd.DataFrame()

    results = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.get_level_values(0):
                    continue
                stock_df = data[symbol].dropna(how="all")
            else:
                if len(asset_list) != 1:
                    continue
                stock_df = data.dropna(how="all")
            status = calculate_stp_uyanis_status(stock_df)
            if not status:
                continue
            results.append({
                "Sembol": symbol,
                "Fiyat": status["current_price"],
                "Durum": status["state_label"],
                "Durum_Kodu": status["state"],
                "Olay_Gunu": status["event_age"],
                "Baski_Gun": status["days_below"],
                "Uzun_Baski": status["long_pressure"],
                "Baski_Sinifi": status["pressure_kind"],
                "Baski_Etiketi": status["pressure_label"],
                "Erken_Izleme": status["early_observation"],
                "Baski_Sira": status["pressure_rank"],
                "Hacim_Kat": status["volume_ratio"],
                "Mum": status["candle"] or "—",
                "Anlamli_Kesis": status["meaningful"],
                "Tetik_Tarihi": status["signal_date"],
                "Gecersizlik": status["signal_low"],
            })
        except Exception as exc:
            log_error("scan_stp_uyanis", exc, symbol)

    if not results:
        return pd.DataFrame()

    rank = {"confirmed": 0, "active": 0, "exit": 0, "t1": 1, "t0": 2,
            "recross_down": 3, "invalid": 4}
    output = pd.DataFrame(results)
    output["_state_rank"] = output["Durum_Kodu"].map(rank).fillna(9)
    output["_pressure_rank"] = pd.to_numeric(output["Baski_Sira"], errors="coerce").fillna(0)
    output = output.sort_values(
        by=["_state_rank", "_pressure_rank", "Baski_Gun", "Olay_Gunu"],
        ascending=[True, False, False, True],
    ).drop(columns=["_state_rank", "_pressure_rank"]).reset_index(drop=True)
    return output


def scan_guclu_donus_batch(asset_list):
    """
    Güçlü Dönüş Adayları — Toplu Tarama Ajanı (v9)
    BIST100 verisini RS hesabı için ayrıca çeker (parquet cache'den — ek Yahoo isteği yok)
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    # ── BIST100 göreceli güç için ────────────────────────────────────────
    bist100_close = None
    try:
        _bist_ticker = "XU100.IS"
        _bist_data   = get_batch_data_cached([_bist_ticker], period="1y")
        if not _bist_data.empty:
            if isinstance(_bist_data.columns, pd.MultiIndex):
                if _bist_ticker in _bist_data.columns.levels[0]:
                    bist100_close = _bist_data[_bist_ticker]['Close'].dropna()
            else:
                bist100_close = _bist_data['Close'].dropna()
    except Exception:
        bist100_close = None

    results   = []
    stock_dfs = []

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    df = data[symbol].dropna()
                    if not df.empty: stock_dfs.append((symbol, df))
            else:
                if len(asset_list) == 1:
                    df = data.dropna()
                    if not df.empty: stock_dfs.append((symbol, df))
        except: continue

    for symbol, df in stock_dfs:
        res = calculate_guclu_donus_adaylari(symbol, df, bist100_close=bist100_close)
        if res: results.append(res)

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results)
    # Skor DESC → RS_Pct DESC → Sweep_Ay DESC → Hacim_10g DESC
    df_out = df_out.sort_values(
        by=['Skor', 'RS_Pct', 'Sweep_Ay', 'Hacim_10g'],
        ascending=[False, False, False, False]
    ).reset_index(drop=True)
    log_scan_signal("guclu_donus", df_out, category=st.session_state.get('category', ''))
    return df_out

def scan_wilder_positive_divergence_batch(asset_list):
    """Wilder RSI pozitif uyumsuzluk — taze, geç kalmamış BIST dönüşleri."""
    bist_assets = [
        str(symbol).upper()
        for symbol in asset_list
        if str(symbol).upper().endswith(".IS")
        and not str(symbol).upper().startswith("XU")
    ]
    if not bist_assets:
        return _empty_wilder_result_frame()

    data = get_batch_data_cached(bist_assets, period="1y")
    if data is None or data.empty:
        return _empty_wilder_result_frame()

    results = []
    for symbol in bist_assets:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.get_level_values(0):
                    continue
                stock_df = data[symbol].dropna(how="all")
            else:
                if len(bist_assets) != 1:
                    continue
                stock_df = data.dropna(how="all")
            result = detect_wilder_positive_divergence(symbol, stock_df)
            if result:
                results.append(result)
        except Exception as exc:
            log_error("scan_wilder_positive_divergence", exc, symbol)

    if not results:
        return _empty_wilder_result_frame()

    df_out = pd.DataFrame(results)
    df_out["_onayli"] = df_out["Durum"].astype(str).str.contains("ONAYLI").astype(int)
    df_out = (
        df_out.sort_values(
            by=["_onayli", "Tetik_Yasi", "Risk_Odul", "RSI_Farki"],
            ascending=[False, True, False, False],
        )
        .drop(columns=["_onayli"])
        .reset_index(drop=True)
    )

    # Ortak olay kimliği: izleme/onay ayrımı değişse bile aynı RSI yolculuğu
    # devam eder. Para akışı burada kapı değil, kalite notudur.
    log_scan_signal(
        "rsi_pozitif_uyumsuzluk",
        df_out,
        category=st.session_state.get("category", ""),
    )

    fresh = df_out[df_out["Tetik_Yasi"] == 0]
    if not fresh.empty:
        is_confirmed = fresh["Durum"].astype(str).str.contains("ONAYLI")
        log_scan_signal(
            "wilder_pozitif_onayli",
            fresh[is_confirmed],
            category=st.session_state.get("category", ""),
        )
        log_scan_signal(
            "wilder_pozitif_izleme",
            fresh[~is_confirmed],
            category=st.session_state.get("category", ""),
        )
    return df_out

def scan_prelaunch_bos(asset_list):
    """Pre-Launch BOS — toplu tarama."""
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty:
        return pd.DataFrame()

    bist100_close = None
    try:
        _bd = get_batch_data_cached(["XU100.IS"], period="1y")
        if not _bd.empty:
            if isinstance(_bd.columns, pd.MultiIndex):
                bist100_close = _bd["XU100.IS"]['Close'].dropna()
            else:
                bist100_close = _bd['Close'].dropna()
    except Exception:
        pass

    results = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.get_level_values(0):
                    continue
                df = data[symbol].dropna()
            else:
                if len(asset_list) != 1:
                    continue
                df = data.dropna()
            if df.empty:
                continue
            res = calculate_prelaunch_bos(symbol, df, bist100_close)
            if res:
                # Güç etiketi (19 Haz 2026) — prelaunch_bos amiral gemisi TIER_1, ama Haziran
                # ayısında 5-10g çöktü (52H dip<%40 en zayıf %40). Güç teyidi AI'a aksın ki
                # zayıf hissede ateşlediğinde "sert kural" körü körüne merkeze taşımasın.
                _pg, _pr, _pp = _harmonik_52h_strength(df)
                res['Guc'] = _pg
                res['Guc_52h'] = _pp
                results.append(res)
        except Exception:
            continue

    if not results:
        return pd.DataFrame()

    df_out = pd.DataFrame(results).sort_values(
        by=['Skor', 'BOS_Day', 'Hacim_Kat'],
        ascending=[False, True, False]
    ).reset_index(drop=True)
    log_scan_signal("prelaunch_bos", df_out, category=st.session_state.get('category', ''))
    return df_out

@st.cache_data(ttl=900)
def scan_ict_batch(asset_list):
    """
    ICT Toplu Tarama Ajanı (Paralel Çalışır)
    """
    # 13 — REJİM KAPISI (4 Tem 2026): 6 aylık backtest 534 sinyal · hit %39 ·
    # PF 0.88 → kanıtlı zayıf. SON ŞANS: sadece endeks SMA50 ÜSTÜNDEYKEN tarar.
    # Eylül 2026 karnesinde hâlâ zayıfsa Sniper emekli edilecek.
    try:
        _bench = get_benchmark_data(st.session_state.get('category', 'BIST'))
        if _bench is not None and len(_bench) >= 50:
            _b_ser = pd.Series(_bench).astype(float)
            if float(_b_ser.iloc[-1]) < float(_b_ser.rolling(50).mean().iloc[-1]):
                return pd.DataFrame()   # ayı rejimi — Sniper sinyal üretmez (rozet açıklar)
    except Exception:
        pass
    # 1. Veri Çek (Cache'den)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()
    
    results = []
    stock_dfs = []
    
    # Veriyi hisselere ayır
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            else:
                if len(asset_list) == 1: stock_dfs.append((symbol, data))
        except: continue

    # 2. Paralel İşleme (Dedektörü Çalıştır)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(process_single_ict_setup, sym, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # 3. Sonuç Döndür
    if results:
        return pd.DataFrame(results)
    
    return pd.DataFrame()

def scan_nadir_firsat_batch(asset_list):
    """
    Royal Flush Nadir Set-up — batch veri + paralel (ThreadPoolExecutor).
    Eski sürüm: 500 sembol × ~2sn sıralı = ~17 dakika.
    Yeni sürüm: batch indir + 10 paralel thread = ~25 saniye.
    """
    # Adım 1: Batch veriyi al (zaten master scan'de indirildi, cache'den gelir)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty:
        return pd.DataFrame()

    # Adım 1b: Endeks bench serisi (RS Alpha hesabı için) — kategoriye göre
    _cat_nf = st.session_state.get('category', '')
    _bench_t_nf = "XU100.IS" if ("BIST" in _cat_nf or any('.IS' in s for s in asset_list[:3])) else "^GSPC"
    _bench_df_nf = None
    try:
        _bench_df_nf = get_safe_historical_data(_bench_t_nf, period="1y")
    except Exception:
        _bench_df_nf = None
    _bench_close_nf = _bench_df_nf['Close'] if (_bench_df_nf is not None and 'Close' in _bench_df_nf.columns) else None

    # Adım 2: Her sembol için DataFrame'i ayır
    stock_dfs = []
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    df_sym = data[symbol].dropna(how='all')
                    if not df_sym.empty:
                        stock_dfs.append((symbol, df_sym))
            else:
                if len(asset_list) == 1:
                    stock_dfs.append((symbol, data.dropna(how='all')))
        except Exception:
            continue

    # Adım 3: Paralel işle (10 thread, sembol başı 5sn timeout)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_nadir_firsat_single_fast, sym, df, _bench_close_nf): sym
            for sym, df in stock_dfs
        }
        for future in concurrent.futures.as_completed(futures):
            try:
                res = future.result(timeout=5)
                if res:
                    results.append(res)
            except Exception:
                continue

    if not results:
        return pd.DataFrame()
    df_nadir = pd.DataFrame(results).sort_values('Sembol').reset_index(drop=True)
    log_scan_signal("nadir_firsat", df_nadir, category=st.session_state.get('category', ''))
    return df_nadir

@st.cache_data(ttl=900)
def scan_minervini_batch(asset_list):
    # 1. Veri İndirme (Hızlı Batch)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()
    
    # 2. Endeks Belirleme
    cat = st.session_state.get('category', 'S&P 500')
    bench = "XU100.IS" if "BIST" in cat else "^GSPC"

    results = []
    stock_dfs = []
    
    # Veriyi hazırlama (Hisselere bölme)
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol in data.columns.levels[0]:
                    stock_dfs.append((symbol, data[symbol]))
            elif len(asset_list) == 1:
                stock_dfs.append((symbol, data))
        except: continue

    # 3. Paralel Tarama (Yukarıdaki sertleştirilmiş fonksiyonu çağırır)
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        # provided_df argümanını kullanarak internetten tekrar indirmeyi engelliyoruz
        futures = [executor.submit(calculate_minervini_sepa, sym, bench, df) for sym, df in stock_dfs]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
            
    # 4. Sıralama ve Kesme
    if results:
        df = pd.DataFrame(results)
        # En yüksek Puanlı ve en yüksek RS'li olanları üste al
        # Sadece ilk 30'u göster ki kullanıcı boğulmasın.
        df_min = df.sort_values(by=["Raw_Score", "rs_val"], ascending=[False, False]).head(30)
        log_scan_signal("minervini", df_min, category=st.session_state.get('category', ''))
        return df_min

    return pd.DataFrame()

@st.cache_data(ttl=900)
def scan_rs_momentum_leaders(asset_list):
    """
    GÜNCELLENMİŞ: RS MOMENTUM + BETA AYARLI ALPHA
    Hız Tuzağına Düşmeden, İşlemci Gücüyle Beta ve Sigma Hesabı Yapar.
    Profesyonel Fon Yöneticisi Mantığı: Beta Adjusted Alpha + Dynamic Sigma Safety Lock.
    """
    # 1. Verileri Çek — Master Scan preload ile cache key uyumu (mismatch fix)
    # (Fonksiyon period parametresini içsel olarak görmezden geliyor, sadece @st.cache_data anahtarı için)
    data = get_batch_data_cached(asset_list, period="1y")
    if data.empty: return pd.DataFrame()

    # 2. Endeks Verisi
    cat = st.session_state.get('category', 'S&P 500')
    bench_ticker = "XU100.IS" if "BIST" in cat else "^GSPC"
    df_bench = get_safe_historical_data(bench_ticker, period="3mo")
    
    if df_bench is None or df_bench.empty: return pd.DataFrame()
    
    # Endeks Performansları ve Getirileri (Beta hesabı için kritik)
    b_close = df_bench['Close']
    bench_returns = b_close.pct_change().dropna() 
    
    # Basit Kıyaslama (Eski yöntem - Referans ve ham hesap için)
    bench_5d = ((b_close.iloc[-1] - b_close.iloc[-6]) / b_close.iloc[-6]) * 100
    bench_1d = ((b_close.iloc[-1] - b_close.iloc[-2]) / b_close.iloc[-2]) * 100

    # Piyasa çöküş filtresi: Endeks günlük -%2'nin altındaysa tarama anlamsız —
    # çöken piyasada tüm hisseler yapay alpha gösterebilir.
    if bench_1d <= -2.0:
        return pd.DataFrame()

    results = []

    # 3. Hisseleri Tara
    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]: continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            # Beta ve Sigma hesabı için en az 60 bar veri lazım
            if len(df) < 60: continue 

            close = df['Close']; volume = df['Volume']
            stock_returns = close.pct_change().dropna()

            # --- A. YENİ NESİL BETA HESAPLAMASI (CPU Hızıyla) ---
            # Hissenin ve Endeksin zaman serilerini eşle (Alignment)
            aligned_stock = stock_returns.reindex(bench_returns.index).dropna()
            aligned_bench = bench_returns.reindex(aligned_stock.index).dropna()
            
            # Kovaryans / Varyans = Beta
            if len(aligned_bench) > 20: # Yeterli ortak gün varsa hesapla
                covariance = np.cov(aligned_stock, aligned_bench)[0][1]
                variance = np.var(aligned_bench)
                beta = covariance / variance if variance != 0 else 1.0
            else:
                beta = 1.0 # Veri yetmezse varsayılan
            
            # --- B. PERFORMANS HESAPLARI ---
            stock_now = float(close.iloc[-1])
            stock_old_5 = float(close.iloc[-6])
            
            # 5 Günlük Performans
            stock_perf_5d = ((stock_now - stock_old_5) / stock_old_5) * 100
            
            # Beta Ayarlı Alpha (Jensen's Alpha Mantığı)
            # Beklenen Getiri = Beta * Endeks Getirisi
            expected_return_5d = bench_5d * beta
            adjusted_alpha_5d = stock_perf_5d - expected_return_5d

            # --- C. DİNAMİK EMNİYET KİLİDİ (SIGMA) ---
            # Hissenin endekse göre "normal" sapmasını bul
            alpha_series = (stock_returns - bench_returns).dropna().tail(20)
            alpha_std = alpha_series.std() * 100 # Yüzde cinsinden standart sapma
            
            # Kilit Eşiği: Kendi oynaklığının 1.5 katı kadar negatif ayrışma
            safety_threshold = -(alpha_std * 1.5)
            
            # Bugünün durumu
            stock_perf_1d = ((stock_now - float(close.iloc[-2])) / float(close.iloc[-2])) * 100
            today_raw_alpha = stock_perf_1d - bench_1d

            # Hacim Kontrolü
            curr_vol = float(volume.iloc[-1])
            avg_vol = float(volume.iloc[-21:-1].mean())
            vol_ratio = curr_vol / avg_vol if avg_vol > 0 else 0

            # --- FİLTRELEME (PROFESYONEL KRİTERLER) ---
            # 1. Beta Ayarlı Alpha > 1.25 (Gerçek Güç)
            # 2. Hacim > 0.9 (İlgi var)
            # 3. Bugün "Güvenli Eşik"ten daha fazla düşmemiş (Momentum Kırılmamış)
            if adjusted_alpha_5d >= 1.25 and vol_ratio > 0.9 and today_raw_alpha > safety_threshold:
                
                results.append({
                    "Sembol": symbol,
                    "Fiyat": stock_now,
                    "Beta": round(beta, 2), # Bilgi için ekranda görünebilir
                    "Alpha_5D": adjusted_alpha_5d,     # İsmi Alpha_5D olarak düzelttik
                    "Adj_Alpha_5D": adjusted_alpha_5d, # Sıralama kriteri
                    "Ham_Alpha_5D": stock_perf_5d - bench_5d, # Eski usül (referans)
                    "Eşik": round(safety_threshold, 2),
                    "Hacim_Kat": vol_ratio,
                    "Skor": adjusted_alpha_5d # Skor artık "Gerçek Alpha"
                })

        except Exception as e: continue

    # 4. Sıralama
    if results:
        # Skora göre azalan sırala
        df_rs = pd.DataFrame(results).sort_values(by="Skor", ascending=False)
        log_scan_signal("rs_leaders", df_rs, category=st.session_state.get('category', ''))
        return df_rs

    return pd.DataFrame()

@st.cache_data(ttl=900)
def scan_harmonic_confluence_batch(asset_list):
    """
    Tüm listede Harmonic + ICT Discount + RSI Div üçlü confluence tarar.
    """
    data = get_batch_data_cached(asset_list, period="1y")
    if data is None or (hasattr(data, 'empty') and data.empty):
        return pd.DataFrame()

    results = []
    _EMOJI = {'Gartley': '🦋', 'Butterfly': '🦋', 'Bat': '🦇', 'Crab': '🦀', 'Shark': '🦈'}

    for symbol in asset_list:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if symbol not in data.columns.levels[0]:
                    continue
                df = data[symbol].dropna()
            else:
                df = data.dropna()

            if len(df) < 60:
                continue

            res = calculate_harmonic_confluence(symbol, df)
            if res:
                emoji = _EMOJI.get(res['pattern'], '🔮')
                dir_e = '🟢' if res['direction'] == 'Bullish' else '🔴'
                # 52H güç filtresi (iki-rejim backtest kanıtlı)
                _guc, _guc_rank, _p52 = _harmonik_52h_strength(df)
                if HARMONIK_HIDE_WEAK and _guc_rank >= 2:
                    continue  # zayıf (dip<%40) sinyali gizle
                _base_badge = res.get('badge_str', '')
                _badge_full = f"{_guc}{(' | ' + _base_badge) if _base_badge else ''}" if _guc else _base_badge
                results.append({
                    'Sembol':    symbol,
                    'Fiyat':     round(res['prz'], 2),
                    'Pattern':   f"{emoji} {res['pattern']}",
                    'Yön':       f"{dir_e} {res['direction']}",
                    'PRZ':       round(res['prz'], 2),
                    'ICT_Zone':  res['zone'],
                    'RSI_Div':   res['div_type'],
                    'Durum':     '✅ Taze' if res['state'] == 'fresh' else '📍 Yaklaşıyor',
                    'Guc':       _guc,
                    'Guc_52h':   _p52,
                    'Badges':    _badge_full,
                    'Aciklama':  res.get('Aciklama', ''),
                    '_guc_rank': _guc_rank,
                })
        except Exception:
            continue

    if not results:
        return pd.DataFrame()
    df_out = pd.DataFrame(results)
    # Sıralama: önce güç (güçlü en üstte), sonra bullish önce
    df_out['_s'] = df_out['Yön'].apply(lambda x: 0 if 'Bullish' in x else 1)
    df_out.sort_values(['_guc_rank', '_s'], inplace=True)
    df_out.drop(columns=['_s', '_guc_rank'], inplace=True)
    df_out.reset_index(drop=True, inplace=True)
    return df_out

def _published_v2_top10_symbols(signal_date):
    """Aynı kapanış tarihindeki yayımlanmış V2 ilk 10 sembollerini salt-okunur al."""
    try:
        day = pd.Timestamp(signal_date).strftime("%Y-%m-%d")
        db_path = Path(DB_FILE).resolve().with_name("patron2.db")
        if not db_path.exists():
            return set()
        uri = f"file:{db_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            rows = connection.execute(
                "SELECT symbol FROM candidates "
                "WHERE engine='v2' AND signal_date=? AND published=1 AND rank<=10",
                (day,),
            ).fetchall()
        return {str(row[0]).strip().upper().replace(".IS", "") for row in rows if row and row[0]}
    except Exception:
        return set()


def scan_erken_radar_batch(asset_list):
    """
    Erken Radar batch tarama — her hisse için evaluate_erken_radar çağrılır,
    eşleşen HER senaryo ayrı satır olarak döner.
    Çıktı: DataFrame — bir hisse 3 senaryo tetiklerse 3 satır gelir.
    Her satır: Sembol, Fiyat, Skor (overall_quality), ScenarioId, ScenarioName, Category, Stars

    PERFORMANS: get_batch_data_cached ile TÜM hisseler tek seferde alınır
    (MultiIndex DataFrame), tek tek get_safe_historical_data çağrısı yapılmaz.
    BIST 500 için ~6sn (eskiden 2-5dk).
    """
    import pandas as pd
    rows = []
    # Endeksleri filtrele (batch download ile gelmesinler)
    stock_list = [t for t in asset_list
                  if not (t.startswith("XU") or t.startswith("^") or
                          t.endswith("=F") or "-USD" in t or t == "GC=F")]
    if not stock_list:
        return pd.DataFrame()

    # XU100 bench bir kez al (parquet'ten, live patch atlanır - flag set)
    try:
        bench_df = get_safe_historical_data("XU100.IS")
    except Exception:
        bench_df = None
    try:
        _v2_top10_symbols = _published_v2_top10_symbols(bench_df.index[-1]) if bench_df is not None else set()
    except Exception:
        _v2_top10_symbols = set()

    # TÜM hisseleri tek batch'te al (MultiIndex DataFrame, cache'li)
    # ⚡ CACHE HIT: asset_list (scan_list ile aynı) → Master Scan başındaki
    # get_batch_data_cached(scan_list) cache'ini doğrudan vurur — stock_list
    # farklı key üretiyordu ve %98'de gereksiz 500 parquet okuma tetikliyordu.
    try:
        batch_data = get_batch_data_cached(asset_list, period="1y")
    except Exception:
        batch_data = None

    def _extract_df(ticker):
        """MultiIndex'ten ticker'ın DataFrame'ini çıkar (yoksa fallback parquet)"""
        if batch_data is not None and not batch_data.empty:
            try:
                if isinstance(batch_data.columns, pd.MultiIndex):
                    # get_level_values(0): actual values (levels[0] frozen olabilir)
                    if ticker in batch_data.columns.get_level_values(0):
                        return batch_data[ticker].dropna(how='all')
                else:
                    return batch_data.dropna(how='all')
            except Exception:
                pass
        # Fallback: tek hisse parquet'ten
        try:
            return get_safe_historical_data(ticker)
        except Exception:
            return None

    for ticker in stock_list:
        try:
            df_hist = _extract_df(ticker)
            if df_hist is None or len(df_hist) < 60:
                continue
            er = evaluate_erken_radar(ticker, df_hist, bench_df)
            if er is None:
                continue
            price = float(df_hist['Close'].iloc[-1])
            quality = er.get('overall_quality', 0) or 0
            sym = ticker.replace('.IS', '')
            # ── ERKEN RADAR GÜÇ ETİKETİ (19 Haz 2026 — iki-rejim backtest kanıtlı) ──
            # 12.381 sinyal (Mayıs boğa + Haziran ayı): RSI>70 olduğunda Erken Radar
            # ayı ayında BİLE çalışır (May %61 / Haz %59 isabet); RSI<40 dipte ise
            # Haziran'da %35'e çöker. Tek-rejim "5★ şampiyon" rakamları boğa şişmesiydi.
            try:
                _ce = df_hist['Close']; _de = _ce.diff()
                _ge = _de.where(_de > 0, 0).rolling(14).mean()
                _le2 = (-_de.where(_de < 0, 0)).rolling(14).mean()
                _rsi_er = float((100 - 100 / (1 + _ge / _le2)).iloc[-1])
                _seg_e = df_hist.tail(252)
                _he, _le = float(_seg_e['High'].max()), float(_seg_e['Low'].min())
                _p52_e = (price - _le) / (_he - _le) * 100 if _he > _le else None
            except Exception:
                _rsi_er, _p52_e = None, None
            if _rsi_er is not None and _rsi_er >= 70:
                _guc_e = '🟢 GÜÇLÜ'
            elif _rsi_er is not None and _rsi_er < 45 and (_p52_e is None or _p52_e < 40):
                _guc_e = '🔴 ZAYIF'
            else:
                _guc_e = '🟡 ORTA'
            _guc_fields = {'Guc': _guc_e, 'Guc_rsi': round(_rsi_er, 1) if _rsi_er is not None else None}

            # 29 Tem 2026 — yalnız B11 ve C6 için seçili derinleştirme katmanı.
            # Ana senaryo kapısı değişmez; ek alanlar kalite/zamanlama notudur.
            _matched_ids = {
                str(item.get("id"))
                for item in (
                    [er.get("primary")] + list(er.get("confirmations") or [])
                )
                if isinstance(item, dict)
            }
            _b11_fields = {}
            if "B11" in _matched_ids:
                _b11 = (er.get("deepening") or {}).get("B11") or b11_pilot_profile(df_hist)
                _b11_fields = {
                    "Kalite_Skoru": _b11["quality_score"],
                    "Kalite": _b11["quality_label"],
                    "Kalite_Detay": _b11["detail"],
                    "Para_Akisi_Skor": _b11["flow"]["score"],
                    "Para_Akisi_Kalite": _b11["flow"]["label"],
                    "Gec_Kalma": _b11["late"]["state"],
                    "Gec_Kalma_Detay": _b11["late"]["detail"],
                    "Yolculuk_Asamasi": _b11["quality_label"],
                    "Yolculuk_Gunu": 1,
                    "Yolculuk_Anahtari": "b11_pilot",
                }
            _c6_fields = {}
            if "C6" in _matched_ids:
                _leader = (er.get("deepening") or {}).get("C6") or leadership_profile(
                    df_hist, bench_df
                )
                _leader_quality = (
                    25
                    if _leader["late_state"] == "GEÇ SİNYAL"
                    else (90 if _leader["leader_age"] <= 3 else 75)
                )
                _c6_fields = {
                    "Kalite_Skoru": _leader_quality,
                    "Kalite": (
                        "GEÇ SİNYAL"
                        if _leader["late_state"] == "GEÇ SİNYAL"
                        else "LİDERLİK TEYİDİ"
                    ),
                    "Kalite_Detay": _leader["detail"],
                    "Liderlik_Yasi": _leader["leader_age"],
                    "Gec_Kalma": _leader["late_state"],
                    "Yolculuk_Asamasi": "C6 TEYİTLİ",
                    "Yolculuk_Gunu": _leader["leader_age"],
                    "Yolculuk_Anahtari": "radar2_c6_liderlik",
                }

            def _deep_fields(scenario_id):
                if str(scenario_id) == "B11":
                    return _b11_fields
                if str(scenario_id) == "C6":
                    return _c6_fields
                return {}

            def _v2_badge_fields(scenario):
                matched = str(scenario.get("id")) == "B11" and sym in _v2_top10_symbols
                badge = "🚀 V2 İlk 10 Teyidi" if matched else ""
                name = str(scenario.get("name", ""))
                return {
                    "ScenarioName": f"{name} · {badge}" if badge else name,
                    "V2_Rozet": badge,
                }

            # Primary
            primary = er.get('primary')
            if primary:
                rows.append({
                    'Sembol': sym,
                    'Fiyat':  round(price, 2),
                    'Skor':   quality,
                    'ScenarioId':   primary['id'],
                    **_v2_badge_fields(primary),
                    'Category':     primary['category'],
                    'Stars':        primary['stars'],
                    'Role':         'primary',
                    **_guc_fields,
                    **_deep_fields(primary['id']),
                })
            # Confirmations
            for c in (er.get('confirmations') or []):
                rows.append({
                    'Sembol': sym,
                    'Fiyat':  round(price, 2),
                    'Skor':   quality,
                    'ScenarioId':   c['id'],
                    **_v2_badge_fields(c),
                    'Category':     c['category'],
                    'Stars':        c['stars'],
                    'Role':         'confirmation',
                    **_guc_fields,
                    **_deep_fields(c['id']),
                })
            # Red flags
            for rf in (er.get('red_flags') or []):
                rows.append({
                    'Sembol': sym,
                    'Fiyat':  round(price, 2),
                    'Skor':   quality,
                    'ScenarioId':   rf['id'],
                    **_v2_badge_fields(rf),
                    'Category':     rf['category'],
                    'Stars':        rf['stars'],
                    'Role':         'red_flag',
                    **_guc_fields,
                    **_deep_fields(rf['id']),
                })
        except Exception:
            continue
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def scan_leadership_lifecycle(radar2_df, erken_radar_df, category=""):
    """Radar2 adayını C6 teyidiyle tek liderlik yaşam döngüsüne bağlar."""
    lifecycle = build_leadership_lifecycle(radar2_df, erken_radar_df)
    persistence_ok = True
    if lifecycle.empty:
        for scan_type in (
            "liderlik_aday",
            "liderlik_yeni",
            "liderlik_teyitli",
            "liderlik_gec",
        ):
            persistence_ok = log_scan_signal(scan_type, pd.DataFrame(), category=category) and persistence_ok
        lifecycle.attrs["_persistence_ok"] = persistence_ok
        return lifecycle
    for scan_type in (
        "liderlik_aday",
        "liderlik_yeni",
        "liderlik_teyitli",
        "liderlik_gec",
    ):
        subset = lifecycle[lifecycle["Liderlik_Tarama"] == scan_type]
        persistence_ok = log_scan_signal(scan_type, subset, category=category) and persistence_ok
    stage_order = {
        "YENİ LİDER": 0,
        "LİDERLİK TEYİTLİ": 1,
        "ADAY": 2,
        "GEÇ SİNYAL": 3,
    }
    lifecycle["_stage_order"] = lifecycle["Liderlik_Asamasi"].map(stage_order).fillna(9)
    lifecycle = (
        lifecycle.sort_values(
            ["_stage_order", "Kalite_Skoru", "Liderlik_Yasi"],
            ascending=[True, False, True],
        )
        .drop(columns=["_stage_order"])
        .reset_index(drop=True)
    )
    lifecycle.attrs["_persistence_ok"] = persistence_ok
    return lifecycle


def log_erken_radar_signals(df_batch, category=""):
    """
    scan_erken_radar_batch çıktısını signals.db'ye yazar.
    Her senaryo ayrı scan_type ile: 'er_A1', 'er_B4', vb.
    Aynı gün + aynı senaryo + aynı sembol kombinasyonu INSERT OR IGNORE ile atlanır.
    """
    if df_batch is None:
        df_batch = pd.DataFrame()
    today = datetime.now(_TZ_ISTANBUL).strftime("%Y-%m-%d")
    # 20 Haz 2026 — ER FEATURE SNAPSHOT: eskiden ER doğrudan INSERT ediyordu (feature BAYPAS) →
    # 14.446 sinyalin 0'ında f_rsi vardı → RSI güç filtresi DENETLENEMİYORDU. Artık her sembol için
    # f_rsi/f_52h_pos/f_master_score yazılıyor (cache'li, sembol başı tek hesap). Böylece ER güç de
    # backtest'le denetlenebilir hale gelir (forward — eski sinyaller point-in-time geri-doldurulamaz).
    _er_feat = {}
    try:
        for _s in df_batch.get('Sembol', pd.Series()).dropna().astype(str).unique():
            if _s and _s not in _er_feat:
                _er_feat[_s] = _compute_signal_features(_s) or {}
    except Exception:
        pass
    conn = None
    _write_guard = None
    try:
        _write_guard = database_write_lock("early_radar_log")
        _write_guard.__enter__()
        conn = sqlite3.connect(DB_FILE, timeout=60)
        ensure_event_schema(conn)
        ensure_deepening_schema(conn)
        c = conn.cursor()
        _counts = {}
        if not df_batch.empty and 'ScenarioId' in df_batch.columns:
            _counts = df_batch['ScenarioId'].astype(str).value_counts().to_dict()
        _previous_runs = {}
        # Erken Radar motoru her çalıştığında bütün senaryolar sınanır. Sonuç sıfır olsa
        # bile çalışma kaydı tutulur; böylece "devam ediyor" ile "ara verip yeniden doğdu"
        # birbirinden ayrılır.
        for _sid in ERKEN_RADAR_SCENARIOS:
            _stype = f"er_{_sid}"
            _previous_runs[_stype] = register_scan_run(
                conn, _stype, today, int(_counts.get(str(_sid), 0)), category
            )
        for _, row in df_batch.iterrows():
            sym = row.get('Sembol', '')
            if not sym:
                continue
            scid = row.get('ScenarioId', '')
            if not scid:
                continue
            scan_type = f"er_{scid}"
            sym_db = str(sym).replace('.IS', '')
            try:
                price = float(row.get('Fiyat', 0))
            except Exception:
                price = None
            try:
                score = float(row.get('Skor', 0))
            except Exception:
                score = None
            stars = row.get('Stars')
            bias = 'bearish' if row.get('Role') == 'red_flag' else 'bullish'
            _ft = _er_feat.get(str(sym), {}) or {}
            c.execute(
                '''INSERT OR IGNORE INTO scan_signals
                   (scan_date, symbol, scan_type, score, bias, entry_price, stop_level, category,
                    f_rsi, f_52h_pos, f_master_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (today, sym_db, scan_type, score, bias, price, None, category,
                 _ft.get('f_rsi'), _ft.get('f_52h_pos'), _ft.get('f_master_score'))
            )
            _q_score = row.get("Kalite_Skoru")
            _q_label = row.get("Kalite")
            _q_detail = row.get("Kalite_Detay")
            _j_stage = row.get("Yolculuk_Asamasi")
            _j_age = row.get("Yolculuk_Gunu")
            _j_key = row.get("Yolculuk_Anahtari")

            def _finite_float(value):
                try:
                    numeric = float(value)
                    return numeric if np.isfinite(numeric) else None
                except (TypeError, ValueError):
                    return None

            def _clean_text(value):
                return str(value) if value is not None and pd.notna(value) else None

            _q_score_clean = _finite_float(_q_score)
            _q_label_clean = _clean_text(_q_label)
            _q_detail_clean = _clean_text(_q_detail)
            _j_stage_clean = _clean_text(_j_stage)
            _j_age_clean = _finite_float(_j_age)
            _j_key_clean = _clean_text(_j_key)
            if any(
                value is not None
                for value in (
                    _q_score_clean,
                    _q_label_clean,
                    _q_detail_clean,
                    _j_stage_clean,
                    _j_age_clean,
                    _j_key_clean,
                )
            ):
                c.execute(
                    """
                    UPDATE scan_signals
                    SET quality_score=?, quality_label=?, quality_detail=?,
                        journey_stage=?, journey_age=?, journey_key=?
                    WHERE scan_date=? AND symbol=? AND scan_type=?
                    """,
                    (
                        _q_score_clean,
                        _q_label_clean,
                        _q_detail_clean,
                        _j_stage_clean,
                        int(_j_age_clean) if _j_age_clean is not None else None,
                        _j_key_clean,
                        today,
                        sym_db,
                        scan_type,
                    ),
                )
        for _stype, _previous in _previous_runs.items():
            assign_event_metadata_for_date(conn, _stype, today, _previous)
        conn.commit()
        conn.close()
        _write_guard.__exit__(None, None, None)
        _write_guard = None
        return True
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.close()
            except Exception:
                pass
        if _write_guard is not None:
            _write_guard.__exit__(type(e), e, e.__traceback__)
            _write_guard = None
        logging.warning(f"[log_erken_radar_signals] HATA: {e}")
        return False

def get_golden_trio_batch_scan(ticker_list):
    # Gerekli tüm kütüphaneleri burada çağırıyoruz
    import pandas as pd
    import time

    # --- YARDIMCI RSI HESAPLAMA FONKSİYONU ---
    def calc_rsi_manual(series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    golden_candidates = []
    platin_candidates = [] # YENİ: Platin Fırsat adayları
    tekli_altin_candidates = [] # Tekli hisse kriterleri (Altın %65 Discount + Platin bayrağı)
    market_caps = load_market_cap_map()

    # 1. BİLGİLENDİRME & HAZIRLIK
    st.toast("Parquet önbellek kullanılıyor (Ban Korumalı Mod)...", icon="⚡")
    progress_text = "📡 Veri Önbelleğinden Okunuyor (get_batch_data_cached)..."
    my_bar = st.progress(10, text=progress_text)

    # 2. ENDEKS VERİSİNİ AL (Hafızadan Çeker)
    index_close = fetch_index_data_cached()

    # 3. TOPLU VERİ — get_batch_data_cached (parquet önbellek, fetch_market_data_cached'den güvenilir)
    try:
        data = get_batch_data_cached(ticker_list, period="1y")
    except Exception as e:
        st.error(f"Veri çekme hatası: {e}")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    my_bar.progress(40, text="⚡ Veriler İşleniyor (Çift Katmanlı Analiz)...")

    # 4. HIZLI ANALİZ DÖNGÜSÜ
    if isinstance(data.columns, pd.MultiIndex):
        valid_tickers = [t for t in ticker_list if t in data.columns.get_level_values(0).unique()]
    else:
        valid_tickers = ticker_list if not data.empty else []

    total_tickers = len(valid_tickers)

    for i, ticker in enumerate(valid_tickers):
        try:
            # Veriyi al
            if isinstance(data.columns, pd.MultiIndex):
                df = data[ticker].copy()
            else:
                df = data.copy()

            # Veri yetersizse atla (SMA200 için en az 200 bar lazım)
            if df.empty or len(df) < 200: continue

            # --- YENİ: DÜŞEN BIÇAK VE TUZAK KALKANI (5 KURAL) ---
            today_c = df['Close'].iloc[-1]
            today_o = df['Open'].iloc[-1]
            today_h = df['High'].iloc[-1]
            today_l = df['Low'].iloc[-1]
            yest_c = df['Close'].iloc[-2]
            yest_o = df['Open'].iloc[-2]
            day2_c = df['Close'].iloc[-3]

            # 1. Kırmızı Mum — filtre değil, uyarı bayrağı
            has_red_candle = today_c < today_o

            # 2. Son 2 Günlük Mikro RS Kalkanı (Dün kırmızı, bugün yeşilse)
            if yest_c < yest_o and today_c >= today_o:
                if index_close is not None and len(index_close) > 3:
                    stock_2d_ret = (today_c / day2_c) - 1
                    index_2d_ret = (index_close.iloc[-1] / index_close.iloc[-3]) - 1
                    if stock_2d_ret < index_2d_ret:
                        continue # Ölü kedi sıçraması, endeksi yenemedi, ele.

            # 3. Göreceli Çöküş Koruması (endeks-bağıl — piyasa geneli düşüşte aşırı elenmesin)
            crash_2d = (today_c - day2_c) / day2_c
            if index_close is not None and len(index_close) >= 3:
                _idx_2d = (float(index_close.iloc[-1]) / float(index_close.iloc[-3])) - 1
                if crash_2d < _idx_2d - 0.03:   # endeksten %3'ten fazla zayıfsa ele
                    continue
            elif crash_2d < -0.08:               # endeks yoksa mutlak %8 eşiği
                continue

            # UYARI BAYRAKLARI (Shooting Star & Doji)
            has_warning = False
            body = abs(today_c - today_o)
            rng = today_h - today_l
            upper_shadow = today_h - max(today_c, today_o)
            lower_shadow = min(today_c, today_o) - today_l

            # 4. Shooting Star (Kayan Yıldız) Uyarısı
            if upper_shadow >= 2 * body and lower_shadow <= body and body > 0:
                has_warning = True

            # 5. Doji Uyarısı
            if rng > 0 and body <= rng * 0.1:
                has_warning = True

            current_price = today_c
            
            # --- KRİTER 1: GÜÇ (RS) — tekli incelemeyle tutarlı ---
            is_powerful = False
            prev_price_rs = df['Close'].iloc[-10]
            rsi_val = calc_rsi_manual(df['Close']).iloc[-1]

            if index_close is not None and len(index_close) > 10:
                stock_ret = (current_price / prev_price_rs) - 1
                index_ret = (index_close.iloc[-1] / index_close.iloc[-10]) - 1
                # Tekli inceleme: endeksi geçti VEYA sentiment>=50 VEYA RSI>50
                if stock_ret > index_ret or rsi_val > 50:
                    is_powerful = True
            else:
                if rsi_val > 50:
                    is_powerful = True

            # --- KRİTER 2: KONUM — tekli incelemeyle tutarlı (3 alternatif kapı) ---
            high_60 = df['High'].rolling(60).max().iloc[-1]
            low_60 = df['Low'].rolling(60).min().iloc[-1]
            range_diff = high_60 - low_60

            is_discount = False
            # Kapı 1: 3 aylık bandın alt %72'sinde
            if range_diff > 0:
                loc_ratio = (current_price - low_60) / range_diff
                if loc_ratio < 0.72:
                    is_discount = True

            # Kapı 2 & 3: BOS veya MSS yapı kırılımı — tekli incelemede de bu alternatif geçerli
            if not is_discount:
                _sw_highs_scan = []
                _sw_lows_scan  = []
                for _si in range(2, min(len(df) - 2, 30)):
                    try:
                        if df['High'].iloc[-_si] >= max(df['High'].iloc[-_si-2:-_si].max(), df['High'].iloc[-_si+1:-_si+3].max()):
                            _sw_highs_scan.append(float(df['High'].iloc[-_si]))
                        if df['Low'].iloc[-_si] <= min(df['Low'].iloc[-_si-2:-_si].min(), df['Low'].iloc[-_si+1:-_si+3].min()):
                            _sw_lows_scan.append(float(df['Low'].iloc[-_si]))
                    except:
                        pass
                if _sw_highs_scan and current_price > _sw_highs_scan[0]:
                    is_discount = True  # BOS yukarı
                elif _sw_lows_scan and current_price < _sw_lows_scan[0]:
                    is_discount = True  # MSS / BOS aşağı

            # --- KRİTER 3: ENERJİ (HACİM / MOMENTUM) ---
            vol_sma20 = df['Volume'].rolling(20).mean().iloc[-1]
            current_vol = df['Volume'].iloc[-1]
            rsi_now = rsi_val  # zaten hesaplandı
            is_energy = (current_vol > vol_sma20 * 1.05) or (rsi_now > 45)

            # === ALTIN FIRSAT ===
            if is_powerful and is_discount and is_energy:
                # Piyasa değeri sadece önceden hazırlanmış yerel depodan okunur.
                # Eksikse None kalır; tarama sırasında Yahoo'ya sorgu yapılmaz.
                mcap = market_caps.get(str(ticker).strip().upper())

                # Patlamaya en hazır olanı saptamak için Teknik Skor Üretimi:
                # RSI Momentumu + Hacim Şiddeti Çarpanı + Göreceli Güç (Endeks Farkı)
                vol_ratio = (current_vol / vol_sma20) if vol_sma20 > 0 else 1
                rs_farki = 0
                if index_close is not None and len(index_close) > 10:
                    rs_farki = ((current_price / prev_price_rs - 1) - (index_close.iloc[-1] / index_close.iloc[-10] - 1)) * 100
                
                teknik_skor = round(rsi_now + (vol_ratio * 15) + rs_farki, 2)

                golden_candidates.append({
                    "Hisse": ticker,
                    "Fiyat": current_price,
                    "M.Cap": mcap,
                    "Teknik_Skor": teknik_skor,
                    "Onay": "🏆 RS Gücü + Ucuz Konum + Güçlü Enerji",
                    "Warning": has_warning,
                    "RedCandle": has_red_candle,
                    "RSI": round(rsi_now, 1),
                    "Discount_Pct": round(((current_price - low_60) / range_diff) * 100, 1) if range_diff > 0 else 0,
                    "is_platin": False,
                })

            # === PLATİN SET-UP — Bağımsız Filtre ===
            # SMA200+50 üstünde + RSI 35-70 + Hacim aktif + Endeksten güçlü (VEYA'lı)
            # SMA50 yükseliyor = hard filter DEĞİL, +10 bonus + 💎💎 marker
            try:
                _c = df['Close']; _v = df['Volume']
                _s200  = float(_c.rolling(200).mean().iloc[-1])
                _s50   = float(_c.rolling(50).mean().iloc[-1])
                _s50_5 = float(_c.rolling(50).mean().iloc[-5])
                _sma50_rising = _s50 > _s50_5

                if (current_price > _s200 and current_price > _s50 and
                        35 <= rsi_now < 70):

                    _v20 = float(_v.rolling(20).mean().iloc[-1])
                    _cur_vol = float(_v.iloc[-1])

                    # Hacim: aktif mi? (ort.×1.05 VEYA RSI>45)
                    _p_hacim = (_v20 > 0 and _cur_vol > _v20 * 1.05) or rsi_now > 45

                    # Endeks kıyası: geçmiş mi? (son 10g VEYA RSI>50)
                    _p_endeks = False
                    if index_close is not None and len(index_close) >= 10:
                        _sr = float(_c.iloc[-1]) / float(_c.iloc[-10]) - 1
                        _ir = float(index_close.iloc[-1]) / float(index_close.iloc[-10]) - 1
                        _p_endeks = (_sr > _ir) or rsi_now > 50
                    else:
                        _p_endeks = rsi_now > 50

                    if _p_hacim and _p_endeks:
                        _vr  = _cur_vol / _v20 if _v20 > 0 else 1
                        _rs  = 0.0
                        if index_close is not None and len(index_close) >= 10:
                            _rs = ((float(_c.iloc[-1]) / float(_c.iloc[-10]) - 1) -
                                   (float(index_close.iloc[-1]) / float(index_close.iloc[-10]) - 1)) * 100
                        _skor = round(rsi_now + (_vr * 15) + _rs + (10 if _sma50_rising else 0), 2)
                        _mcap_p = market_caps.get(str(ticker).strip().upper())
                        platin_candidates.append({
                            "Hisse":         ticker,
                            "Fiyat":         round(current_price, 2),
                            "M.Cap":         _mcap_p,
                            "Teknik_Skor":   _skor,
                            "Hazırlık":      "✓",
                            "Kurulum":       "SMA200+50 Üstü · Hacim Aktif · Endeksten Güçlü",
                            "Onay":          "💎 PLATİN SET-UP: Güçlü Trend + Hacim + Endeks",
                            "Warning":       has_warning,
                            "RedCandle":     has_red_candle,
                            "SMA50_Rising":  _sma50_rising,
                        })
            except:
                pass

            # === TEKLİ KRİTER TARAMA (NEAR-MISS: Altın 2/3 veya Platin 4/6 - 5/6) ===
            # ELİT eşiğinde duran "kıl payı kaçırmış" hisseler. Tam ELİT olanlar
            # (Altın 3/3, Platin 6/6) zaten Altın/Platin Set-up listelerinde gözükür.
            try:
                # 1) Altın 3 kriteri (yukarıda hesaplandı: is_powerful, is_discount, is_energy)
                _alt_count = int(is_powerful) + int(is_discount) + int(is_energy)
                _alt_missing = []
                if not is_powerful: _alt_missing.append("Güç (endeks/RSI)")
                if not is_discount: _alt_missing.append("Konum (üst aralık)")
                if not is_energy:   _alt_missing.append("Enerji (hacim/momentum)")

                # 2) Platin ek 3 kriteri (SMA200, SMA50, RSI<70)
                _t_sma200_v = float(df['Close'].rolling(200).mean().iloc[-1])
                _t_sma50_v  = float(df['Close'].rolling(50).mean().iloc[-1])
                _ext_sma200 = current_price > _t_sma200_v
                _ext_sma50  = current_price > _t_sma50_v
                _ext_rsi    = rsi_now < 70
                _ext_count  = int(_ext_sma200) + int(_ext_sma50) + int(_ext_rsi)
                _ext_missing = []
                if not _ext_sma200: _ext_missing.append("SMA200 altında")
                if not _ext_sma50:  _ext_missing.append("SMA50 altında")
                if not _ext_rsi:    _ext_missing.append(f"RSI {round(rsi_now)} (≥70)")

                _total = _alt_count + _ext_count

                # 3) Karar — sadece eşikteki near-miss'leri ekle
                _tekli_lbl  = ""
                _tekli_eks  = ""
                _tekli_isp  = False
                _tekli_tier = 0

                if _alt_count == 3 and _total == 5:
                    _tekli_lbl  = "Platin 5/6"
                    _tekli_eks  = " · ".join(_ext_missing) if _ext_missing else ""
                    _tekli_isp  = True
                    _tekli_tier = 5
                elif _alt_count == 3 and _total == 4:
                    _tekli_lbl  = "Platin 4/6"
                    _tekli_eks  = " · ".join(_ext_missing) if _ext_missing else ""
                    _tekli_isp  = True
                    _tekli_tier = 4
                elif _alt_count == 2:
                    _tekli_lbl  = "Altın 2/3"
                    _tekli_eks  = " · ".join(_alt_missing) if _alt_missing else ""
                    _tekli_isp  = False
                    _tekli_tier = 2
                # diğer durumlar (6/6 ELİT Platin, 3/6 sadece Altın, 0-1/3 eleme) → skip

                if _tekli_lbl:
                    _t_vol_sma20 = float(df['Volume'].rolling(20).mean().iloc[-1])
                    _t_cur_vol   = float(df['Volume'].iloc[-1])
                    _t_vr        = (_t_cur_vol / _t_vol_sma20) if _t_vol_sma20 > 0 else 1
                    _t_rs        = 0.0
                    if index_close is not None and len(index_close) > 10:
                        _t_rs = ((current_price / df['Close'].iloc[-10] - 1) -
                                 (index_close.iloc[-1] / index_close.iloc[-10] - 1)) * 100
                    _t_skor = round(rsi_now + (_t_vr * 15) + _t_rs + (_total * 5), 2)
                    _t_disc_pct = round(((current_price - low_60) / range_diff) * 100, 1) if range_diff > 0 else 0

                    # Darvas kutu check
                    _t_dq = _t_ds = _t_dt = _t_db = _t_da = _t_dc = None
                    try:
                        _t_dbox = detect_darvas_box(df)
                        if _t_dbox is not None:
                            _t_dq = _t_dbox['quality']
                            _t_ds = _t_dbox['status']
                            _t_dt = _t_dbox['box_top']
                            _t_db = _t_dbox['box_bottom']
                            _t_da = _t_dbox['box_age']
                            _t_dc = _t_dbox['breakout_class']
                    except:
                        pass

                    tekli_altin_candidates.append({
                        "Hisse":          ticker,
                        "Fiyat":          round(current_price, 2),
                        "Teknik_Skor":    _t_skor,
                        "Etiket":         _tekli_lbl,
                        "Eksik":          _tekli_eks,
                        "Tier":           _tekli_tier,
                        "is_platin":      _tekli_isp,
                        "Discount_Pct":   _t_disc_pct,
                        "RSI":            round(rsi_now, 1),
                        "Warning":        has_warning,
                        "RedCandle":      has_red_candle,
                        "Darvas_Quality": _t_dq,
                        "Darvas_Status":  _t_ds,
                        "Darvas_Top":     _t_dt,
                        "Darvas_Bottom":  _t_db,
                        "Darvas_Age":     _t_da,
                        "Darvas_Class":   _t_dc,
                    })
            except:
                pass

        except:
            continue

        if i % 10 == 0 and total_tickers > 0:
            prog = int((i / total_tickers) * 100)
            my_bar.progress(40 + int(prog/2), text=f"⚡ Analiz: {ticker}...")

    my_bar.progress(100, text="✅ Tarama Tamamlandı! Listeleniyor...")
    time.sleep(0.3)
    my_bar.empty()

    return pd.DataFrame(golden_candidates), pd.DataFrame(platin_candidates), pd.DataFrame(tekli_altin_candidates)
