# -*- coding: utf-8 -*-
"""Master Scan'in ekran bağımsız orkestrasyon çekirdeği.

Bu modül tarama sırasını yönetir; Streamlit, tarayıcı veya sayfa yaşam döngüsü
bilmez. ``durum`` düz sözlük gibi çalışan oturum durumudur, ``bildir`` ise
``(seviye, metin)`` biçimindeki tek bildirim kapısıdır. Uygulamaya özgü küçük
yardımcılar, app.py veya golden_record tarafından ``configure_services`` ile
bağlanır; hesap motorları bu dosyada yeniden yazılmaz.
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

import pandas as pd
import patron_db_guard
import kapanis_master_otomasyon
import master_scan_progress


_SERVICE_NAMESPACE: Mapping[str, Any] | None = None

_MS_PHASE1_STEPS = [
    'index_health', 'backfill', 'mkk', 'data', 'magic_ribbon', 'hidden_accum',
    'formasyon', 'cizgi_yapi', 'minervini', 'rsi_divergence', 'prelaunch',
    'early_radar', 'toplu_terazi',
]
_MS_PHASE2_LABELS = {
    'golden': 'Altın + Platin fırsatlar',
    'radar2': 'Pozitif Karne — Radar 2',
    'weak_pair': 'ICT Sniper + Royal Flush',
    'radar1': 'Ön filtre — Radar 1',
    'strong_reversal': 'Güçlü Dönüş adayları',
    'tavan': 'Tavan adayları — alarm + TOP 30',
    'flow_leaders': 'Para Akışı Liderleri',
    'stp_uyanis': 'STP teyitli tepki · gözlem havuzu',
    'top20': 'TOP 20 & Confluence',
}


def configure_services(namespace: Mapping[str, Any]) -> None:
    """app.py/golden_record tanımlarını ekran motoruna bağlar."""
    if not hasattr(namespace, 'get'):
        raise TypeError('Master Scan servis alanı mapping olmalı')
    global _SERVICE_NAMESPACE
    _SERVICE_NAMESPACE = namespace


def _service(name: str) -> Any:
    if _SERVICE_NAMESPACE is None or name not in _SERVICE_NAMESPACE:
        raise RuntimeError(f'Master Scan bağımlılığı eksik: {name}')
    return _SERVICE_NAMESPACE[name]


def _state_get(durum: Mapping[str, Any], key: str, default: Any = None) -> Any:
    return durum.get(key, default)


def _notify(bildir: Callable[[str, str], Any], level: str, text: str) -> None:
    if not callable(bildir):
        raise TypeError('Master Scan bildir kanalı çağrılabilir olmalı')
    bildir(level, str(text))


def _dry_run(durum: Mapping[str, Any]) -> bool:
    return bool(_state_get(durum, '_ms_dry_run', False))


def _record_error(durum: Mapping[str, Any], key: str, exc: Exception,
                  category: str = '') -> None:
    if _dry_run(durum):
        logging.error('[%s] %s', key, exc)
        return
    _service('log_error')(key, exc, category)


def _persist(durum: Mapping[str, Any], name: str, *args: Any,
             dry_default: Any = None, **kwargs: Any) -> Any:
    """Bilinen yazma noktalarını kuru koşuda gerçekten devre dışı bırakır."""
    if _dry_run(durum):
        return dry_default
    return _service(name)(*args, **kwargs)


def _release_scan_start(durum: Mapping[str, Any]) -> None:
    if not _dry_run(durum):
        kapanis_master_otomasyon.release_scan_start()


def _record_component(durum: Mapping[str, Any], *args: Any, **kwargs: Any) -> Any:
    if _dry_run(durum):
        return None
    return patron_db_guard.record_component_result(*args, **kwargs)


def _get_progress(durum: dict[str, Any]) -> master_scan_progress.MasterScanProgress:
    progress = durum.get('_ms_progress')
    if isinstance(progress, master_scan_progress.MasterScanProgress):
        return progress
    steps = list(durum.get('_ms_engine_progress_steps') or
                 durum.get('progress_steps') or _MS_PHASE1_STEPS)
    progress = master_scan_progress.MasterScanProgress(
        durum.get('_ms_engine_category', durum.get('category', '')), steps,
    )
    durum['_ms_progress'] = progress
    return progress


def _begin_progress(durum: dict[str, Any], bildir: Callable[[str, str], Any],
                    key: str, label: str) -> None:
    progress = _get_progress(durum)
    pct, text = progress.begin(key, label)
    durum['_ms_progress_value'] = pct
    _notify(bildir, 'progress', text)


def _finish_progress(durum: dict[str, Any], bildir: Callable[[str, str], Any]) -> None:
    progress = _get_progress(durum)
    pct, text = progress.finish()
    durum['_ms_progress_value'] = pct
    _notify(bildir, 'progress', text)


def _ms_write_frontend_preview(category: str, er_df: Any) -> None:
    """Erken Radar önizlemesini eski Master Scan kapanış noktasında üretir."""
    if 'BIST' not in str(category).upper() or er_df is None or er_df.empty:
        return
    try:
        _er5 = er_df[
            (er_df['Role'] == 'primary') &
            (er_df['Stars'].apply(lambda value: isinstance(value, int) and value == 5))
        ]
        _all_primary = er_df[er_df['Role'] == 'primary']
        _aging_pairs = [
            (str(row['Sembol']), str(row['ScenarioId'])) for _, row in _er5.iterrows()
        ]
        _aging_map = _service('get_scenario_ages_batch')(
            _aging_pairs, max_lookback=180, include_details=True
        )
        _preview_items = []
        for _, row in _er5.iterrows():
            _ticker = str(row.get('Sembol', '')).replace('.IS', '')
            if not _ticker:
                continue
            _life = _aging_map.get(
                (row.get('Sembol', ''), row.get('ScenarioId', '')), {}
            )
            _preview_items.append({
                'ticker': _ticker,
                'scenario_id': str(row.get('ScenarioId', '')),
                'scenario_name': str(row.get('ScenarioName', '')),
                'category': str(row.get('Category', '')),
                'stars': 5,
                'aging_days': int(_life.get('event_day') or 1),
                'event_day': int(_life.get('event_day') or 1),
                'first_seen': _life.get('first_seen'),
            })
        _preview_items.sort(key=lambda item: item['aging_days'], reverse=True)
        _show_n = 3
        _preview_payload = {
            'generated_at': datetime.now(_service('_TZ_ISTANBUL')).strftime('%Y-%m-%d %H:%M'),
            'category': category,
            'public_items': _preview_items[:_show_n],
            'locked_count': max(0, len(_all_primary) - _show_n),
        }
        _frontend_dir = Path(r'C:\Users\LENOVO\OneDrive\Desktop\Patron Terminal\public\frontend')
        (_frontend_dir / 'erken_radar_preview.json').write_text(
            json.dumps(_preview_payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )
    except Exception as _preview_exc:
        logging.warning('[erken_radar_preview] JSON üretim hatası: %s', _preview_exc)


def run_phase1(durum: dict[str, Any], bildir: Callable[[str, str], Any]) -> bool:
    """Master Scan Faz 1'i mevcut sıra ve sonuç anahtarlarıyla çalıştırır."""
    _cat = str(durum.get('_ms_engine_category', durum.get('category', '')))
    _scan_list = list(durum.get('_ms_engine_scan_list') or durum.get('scan_list') or [])
    _ms_is_bist = bool(durum.get('_ms_engine_is_bist', 'BIST' in _cat.upper()))
    if not _cat:
        raise ValueError('Master Scan kategorisi boş')
    if not _scan_list:
        raise ValueError(f'Master Scan evreni boş: {_cat}')

    _ms_progress_steps = list(
        durum.get('_ms_engine_progress_steps') or durum.get('progress_steps') or
        (_MS_PHASE1_STEPS + [
            'golden', 'radar2', 'weak_pair', 'radar1', 'strong_reversal', 'tavan',
            *(['flow_leaders'] if _ms_is_bist else []), 'stp_uyanis', 'top20',
        ])
    )
    durum['_ms_engine_category'] = _cat
    durum['_ms_engine_scan_list'] = list(_scan_list)
    durum['_ms_engine_is_bist'] = _ms_is_bist
    durum['_ms_engine_progress_steps'] = list(_ms_progress_steps)
    durum['category'] = _cat
    durum['is_bist'] = _ms_is_bist
    _ms_persistence_failures: list[str] = []

    try:
        _begin_progress(durum, bildir, 'index_health', 'XU100 veri sağlığı kontrol ediliyor')

        if 'BIST' in _cat:
            try:
                _xu_durum, _xu_sebep = _service('kritik_endeks_kapisi')('XU100.IS')
            except Exception as _xge:
                _xu_durum, _xu_sebep = 'yellow', [f'kapı hatası: {_xge}']
            if _xu_durum == 'red':
                durum['_master_scan_running'] = False
                _release_scan_start(durum)
                _notify(
                    bildir, 'clear', '',
                )
                _notify(
                    bildir, 'error',
                    "🛑 **Master Scan başlatılmadı: XU100 son kapanışı doğrulanamadı.**\n\n"
                    + " · ".join(_xu_sebep)
                    + "\n\nOrtak terazi bozukken tüm hisselerin endekse-göre gücü yanlış "
                    "çıkar. Yanlış sonuç üretmemek için tarama durduruldu; veri düzeldiğinde "
                    "tekrar deneyin."
                )
                return False
            if _xu_durum == 'yellow':
                _notify(
                    bildir, 'warning',
                    "🟡 **XU100 tek kaynak** (ikinci kaynak doğrulanamadı): "
                    + " · ".join(_xu_sebep)
                    + " — tarama sürüyor, sonuçları dikkatle yorumlayın."
                )

        # 0. Geçmiş sinyal getirileri.
        _begin_progress(durum, bildir, 'backfill', '📊 Geçmiş sinyal getirileri güncelleniyor')
        try:
            _bf_filled, _bf_skipped = _persist(
                durum, 'backfill_signal_returns', dry_default=(0, 0),
            ) or (0, 0)
            if _bf_filled > 0:
                logging.info('[backfill] %s getiri satırı eklendi, %s atlandı.',
                             _bf_filled, _bf_skipped)
        except Exception as _bf_exc:
            _record_error(durum, 'master_scan_backfill', _bf_exc, _cat)

        # 0.5 MKK yabancı cache refresh.
        _begin_progress(durum, bildir, 'mkk', '🏛 Yabancı net akış (MKK) güncelleniyor')
        try:
            if 'BIST' in _cat and not _dry_run(durum):
                _mkk_ok, _mkk_fail = _service('_fetch_mkk_yabanci_rss')(max_days=30)
                logging.info('[mkk] yabanci RSS: %s rapor günü, %s hata.', _mkk_ok, _mkk_fail)
            elif 'BIST' in _cat:
                logging.info('[mkk] kuru koşu: MKK yenilemesi patron.db yazmamak için atlandı.')
        except Exception as _kf_exc:
            _record_error(durum, 'kurumsal_fetch_master_scan', _kf_exc, _cat)

        # 1. Veri fotoğrafı.
        _begin_progress(durum, bildir, 'data', '📡 Veriler indiriliyor (batch)')
        _batch_loader = _service('get_batch_data_cached')
        _batch_loader.clear()
        _master_batch_snapshot = _batch_loader(_scan_list, period='1y')
        _master_snapshot_as_of = datetime.now(_service('_TZ_ISTANBUL')).isoformat()
        _master_benchmark_snapshot = _service('get_safe_historical_data')(
            'XU100.IS', period='1y'
        )
        _master_formasyon_snapshot = pd.DataFrame()
        _master_formasyon_ready = False

        # Magic Ribbon.
        _begin_progress(durum, bildir, 'magic_ribbon',
                        '⏱ BIST100 seans-mumu yukarı hizalanma')
        try:
            _mr_df = (
                _service('scan_magic_ribbon_bist100')()
                if _ms_is_bist and bool(_service('_MAGIC_RIBBON_OK'))
                else pd.DataFrame()
            )
            try:
                if _mr_df is not None and not _mr_df.empty:
                    _persist(durum, 'log_scan_signal',
                             'magic_ribbon_bist_session', _mr_df, category=_cat)
                    if not _dry_run(durum):
                        _service('_magic_ribbon_kaydet')(_mr_df)
            except Exception as _mr_log_exc:
                _record_error(durum, 'master_scan_magic_ribbon_session_log',
                              _mr_log_exc, _cat)
            durum['magic_ribbon_session_data'] = (
                _mr_df if bool(_service('MAGIC_RIBBON_BIST_SESSION_RENDER_ENABLED'))
                else pd.DataFrame()
            )
        except Exception as _magic_ribbon_exc:
            durum['magic_ribbon_session_data'] = pd.DataFrame()
            _record_error(durum, 'master_scan_magic_ribbon_session',
                          _magic_ribbon_exc, _cat)

        # 1.5 Veri sanity monitor.
        try:
            _sanity = _service('_data_sanity_report')(_scan_list)
            durum['_data_sanity_report'] = _sanity
            if not _sanity['ok']:
                for _warning in _sanity['warnings']:
                    logging.warning('[sanity] %s', _warning)
                _notify(
                    bildir, 'warning',
                    "⚠️ **Veri Sağlık Uyarısı** — " + " · ".join(_sanity['warnings'])
                    + f"\n\n_Örneklenen: {_sanity['samples_checked']} hisse._"
                )
        except Exception as _sanity_exc:
            _record_error(durum, 'data_sanity_report', _sanity_exc, _cat)

        # Faz 1: ortak Tarama Merkezi fotoğrafı. Sıra bilinçli olarak korunur.
        _begin_progress(durum, bildir, 'hidden_accum', '🤫 Ölçüm — Gizli Toplama')
        durum['accum_data'] = _service('scan_hidden_accumulation')(_scan_list)
        _persist(durum, 'log_scan_signal', 'gizli_birikim', durum['accum_data'], category=_cat)

        durum['harmonic_confluence_data'] = pd.DataFrame()
        durum['rs_leaders_data'] = pd.DataFrame()

        _begin_progress(durum, bildir, 'formasyon', '📐 Formasyon fotoğrafı (Terazi girdisi)')
        durum['golden_pattern_data'] = {
            'formations': pd.DataFrame(), 'hazirlik': pd.DataFrame(),
        }
        try:
            if getattr(_service('scan_pipeline_mod'), '_BIRLESIK_ON', False):
                _bir_all = _service('scan_chart_patterns')(_scan_list)
                _master_formasyon_snapshot = (
                    _bir_all.copy() if _bir_all is not None else pd.DataFrame()
                )
                _master_formasyon_ready = True
                if _bir_all is not None and not _bir_all.empty and 'ChartData' in _bir_all.columns:
                    _is_bir = _bir_all['ChartData'].apply(
                        lambda value: isinstance(value, dict)
                        and value.get('type') == 'birlesik'
                        and value.get('state') in ('YAKIN', 'KIRILDI')
                    )
                    _bir_act = _bir_all[_is_bir].copy()
                    _ky_rows = []
                    for _, _kr in _bir_act.iterrows():
                        _kd = _kr.get('ChartData') or {}
                        _klvl, _kpr = _kd.get('level'), _kr.get('Fiyat')
                        _kdist = (abs(_klvl - _kpr) / _kpr * 100.0
                                  if (_klvl and _kpr) else None)
                        _ky_rows.append({
                            'Sembol': _kr.get('Sembol'), 'Formasyon': _kr.get('Formasyon'),
                            'Durum': _kd.get('state'),
                            'Yon': '▲' if _kd.get('bias') == 'bullish' else '▼',
                            'Mesafe': round(_kdist, 1) if _kdist is not None else 99.0,
                            'Skor': _kr.get('Skor'), 'ChartData': _kd,
                        })
                    _ky_df = pd.DataFrame(_ky_rows)
                    if not _ky_df.empty:
                        _ky_df['_ord'] = _ky_df['Durum'].map(
                            {'KIRILDI': 0, 'YAKIN': 1}
                        ).fillna(2)
                        _ky_df = _ky_df.sort_values(
                            ['_ord', 'Mesafe']
                        ).drop(columns='_ord').reset_index(drop=True)
                    durum['kirilima_yakin_form'] = _ky_df
                    if not _bir_act.empty:
                        _bir_act['_shape'] = _bir_act['ChartData'].apply(
                            lambda value: value.get('shape')
                        )
                        for _shape in _bir_act['_shape'].dropna().unique():
                            _sub = _bir_act[_bir_act['_shape'] == _shape].copy()
                            _sub['bias'] = _sub['ChartData'].apply(
                                lambda value: (
                                    value.get('bias') if isinstance(value, dict)
                                    and value.get('bias')
                                    else ('bearish' if _shape == 'dtri' else 'bullish')
                                )
                            )
                            _persist(durum, 'log_scan_signal',
                                     f'birlesik_{_shape}', _sub, category=_cat)
        except Exception as _bir_exc:
            _record_error(durum, 'master_scan_birlesik_log', _bir_exc, _cat)
        durum['formasyon_master_data'] = _master_formasyon_snapshot

        # Çizgi Yapısı ortak fotoğrafı.
        _begin_progress(durum, bildir, 'cizgi_yapi', '📐 Çizgi Yapısı — ortak veri fotoğrafı')
        try:
            _line_bist100 = (
                _service('load_index_components')('XU100', allow_network=False)
                if _ms_is_bist else []
            )
            _cizgi_master = _service('cizgi_master')
            if _cizgi_master is None:
                raise RuntimeError('Çizgi Yapısı Master Scan köprüsü yüklenemedi.')
            _cizgi_yapi = _service('cizgi_yapi')
            durum['cizgi_yapi_master_data'] = _cizgi_master.scan_batch_snapshot(
                _master_batch_snapshot,
                _scan_list,
                timeframe='1d',
                lik_taban=(_cizgi_yapi.LIK_TABAN_VARSAYILAN if _ms_is_bist else 0),
                bist100_symbols=_line_bist100,
            )
            try:
                if not _dry_run(durum):
                    _cizgi_yapi.kaydet(durum['cizgi_yapi_master_data'])
            except Exception as _cizgi_save_exc:
                _record_error(durum, 'master_scan_cizgi_yapi_kaydet',
                              _cizgi_save_exc, _cat)
            _notify(
                bildir, 'toast',
                f"📐 Çizgi Yapısı: {len(durum['cizgi_yapi_master_data'])} aday",
            )
        except Exception as _line_exc:
            durum['cizgi_yapi_master_data'] = []
            _record_error(durum, 'master_scan_cizgi_yapi', _line_exc, _cat)

        _begin_progress(durum, bildir, 'minervini', '🦁 Minervini SEPA')
        durum['minervini_data'] = _service('scan_minervini_batch')(_scan_list)
        _begin_progress(durum, bildir, 'rsi_divergence',
                        '🧭 Ölçümde — RSI Pozitif Uyumsuzluk')
        durum['wilder_divergence_data'] = _service(
            'scan_wilder_positive_divergence_batch'
        )(_scan_list)

        _begin_progress(durum, bildir, 'prelaunch', '🔬 Ölçüm — Pre-Launch BOS')
        durum['prelaunch_bos_data'] = _service('scan_prelaunch_bos')(_scan_list)
        _begin_progress(durum, bildir, 'early_radar',
                        '⭐ Senaryo Bazlı Karne — Erken Radar')
        _er_batch_df = _service('scan_erken_radar_batch')(_scan_list)
        _er_logged = _persist(
            durum, 'log_erken_radar_signals', _er_batch_df, category=_cat,
            dry_default=True,
        )
        if not _er_logged:
            _ms_persistence_failures.append('Erken Radar')
        try:
            _er_row_count = len(_er_batch_df) if _er_batch_df is not None else 0
            _record_component(
                durum, 'Erken Radar', bool(_er_logged), category=_cat,
                expected_count=_er_row_count, actual_count=_er_row_count,
            )
        except Exception as _er_karne_exc:
            _ms_persistence_failures.append('Erken Radar karne')
            _record_error(durum, 'master_scan_er_karne', _er_karne_exc, _cat)
        durum['erken_radar_data'] = _er_batch_df
        _persist(durum, 'save_scan_result', 'erken_radar_data', _er_batch_df, _cat)

        _goldmine_logged = _persist(
            durum, 'log_goldmine_selection', category=_cat, top_n=20,
            dry_default=True,
        )
        if not _goldmine_logged:
            _ms_persistence_failures.append('Gold Mine')
        try:
            _record_component(durum, 'Gold Mine', bool(_goldmine_logged), category=_cat)
        except Exception as _gm_karne_exc:
            _ms_persistence_failures.append('Gold Mine karne')
            _record_error(durum, 'master_scan_goldmine_karne', _gm_karne_exc, _cat)

        _begin_progress(durum, bildir, 'toplu_terazi',
                        '⚖️ Dar aday havuzu için Toplu Terazi')
        try:
            durum['toplu_terazi_data'] = _service('_compute_toplu_terazi_snapshot')(
                _master_batch_snapshot,
                _master_benchmark_snapshot,
                _master_formasyon_snapshot,
                _cat,
                as_of=_master_snapshot_as_of,
                formation_ready=_master_formasyon_ready,
            )
        except Exception as _tt_exc:
            durum['toplu_terazi_data'] = {
                'schema_version': _service('TOPLU_TERAZI_SCHEMA_VERSION'),
                'status': 'not_ready',
                'message': f'Toplu Terazi üretilemedi: {_tt_exc}',
                'as_of': _master_snapshot_as_of,
                'category': str(_cat),
                'items': {},
                'errors': [],
            }
            _record_error(durum, 'master_scan_toplu_terazi', _tt_exc, _cat)

        _ms_phase2_steps = [
            'golden', 'radar2', 'weak_pair', 'radar1', 'strong_reversal', 'tavan',
            *(['flow_leaders'] if _ms_is_bist else []), 'stp_uyanis', 'top20',
        ]
        durum['_ms_faz2_baglam'] = {
            'category': _cat,
            'scan_list': list(_scan_list),
            'is_bist': _ms_is_bist,
            'master_batch_snapshot': _master_batch_snapshot,
            'master_benchmark_snapshot': _master_benchmark_snapshot,
            'master_formasyon_snapshot': _master_formasyon_snapshot,
            'master_snapshot_as_of': _master_snapshot_as_of,
            'master_formasyon_ready': _master_formasyon_ready,
            'persistence_failures': list(_ms_persistence_failures),
            'phase1_steps': list(_MS_PHASE1_STEPS),
            'phase2_steps': list(_ms_phase2_steps),
            'progress_steps': list(_ms_progress_steps),
        }
        durum['_ms_faz2_bekliyor'] = list(_ms_phase2_steps)
        durum['_ms_faz2_resume_once'] = False
        durum['_ms_faz2_interruptions'] = 0
        return True
    except Exception as _phase1_exc:
        durum['_master_scan_running'] = False
        _release_scan_start(durum)
        _record_error(durum, 'master_scan_phase1', _phase1_exc, _cat)
        _notify(bildir, 'error', f'Tarama sırasında bir hata oluştu: {str(_phase1_exc)}')
        return False


def run_phase2_step(step: str, durum: dict[str, Any], ctx: dict[str, Any]) -> None:
    """Faz 2'nin tek adımını çalıştırır; sıra çağıran tarafta korunur."""
    _cat = ctx['category']
    _scan_list = ctx['scan_list']

    if step == 'golden':
        _df_golden, _df_nadir, _df_tekli = _service('get_golden_trio_batch_scan')(_scan_list)
        durum['golden_results'] = (
            _df_golden.sort_values(by='Teknik_Skor', ascending=False).reset_index(drop=True)
            if not _df_golden.empty else pd.DataFrame()
        )
        durum['platin_results'] = (
            _df_nadir.sort_values(by='Teknik_Skor', ascending=False).reset_index(drop=True)
            if not _df_nadir.empty else pd.DataFrame()
        )
        durum['tekli_altin_results'] = (
            _df_tekli.sort_values(
                by=['is_platin', 'Teknik_Skor'], ascending=[False, False]
            ).reset_index(drop=True)
            if not _df_tekli.empty else pd.DataFrame()
        )
        _persist(durum, 'log_scan_signal', 'altin_setup', durum['golden_results'], category=_cat)
        _persist(durum, 'log_scan_signal', 'platin_setup', durum['platin_results'], category=_cat)
        _persist(durum, 'log_scan_signal', 'tekli_altin', durum['tekli_altin_results'], category=_cat)
        return

    if step == 'radar2':
        durum['radar2_data'] = _service('radar2_scan')(_scan_list)
        _persist(durum, 'log_scan_signal', 'radar2', durum['radar2_data'], category=_cat)
        durum['liderlik_yolculugu_data'] = _service('scan_leadership_lifecycle')(
            durum.get('radar2_data'), durum.get('erken_radar_data'), category=_cat,
        )
        if not durum['liderlik_yolculugu_data'].attrs.get('_persistence_ok', True):
            ctx['persistence_failures'].append('Liderlik')
        try:
            _leader_ok = bool(
                durum['liderlik_yolculugu_data'].attrs.get('_persistence_ok', True)
            )
            _leader_row_count = len(durum['liderlik_yolculugu_data'])
            _record_component(
                durum, 'Liderlik', _leader_ok, category=_cat,
                expected_count=_leader_row_count, actual_count=_leader_row_count,
            )
        except Exception as _leader_exc:
            ctx['persistence_failures'].append('Liderlik karne')
            _record_error(durum, 'master_scan_liderlik_karne', _leader_exc, _cat)
        return

    if step == 'weak_pair':
        durum['ict_scan_data'] = pd.DataFrame()
        durum['nadir_firsat_scan_data'] = pd.DataFrame()
        return

    if step == 'radar1':
        durum['scan_data'] = _service('analyze_market_intelligence')(_scan_list, _cat)
        _persist(durum, 'log_scan_signal', 'radar1', durum['scan_data'], category=_cat)
        return

    if step == 'strong_reversal':
        durum['guclu_donus_data'] = _service('scan_guclu_donus_batch')(_scan_list)
        return

    if step == 'tavan':
        try:
            import datetime as _tav_dt
            _tav_now = _tav_dt.datetime.now()
            _tav_ck = (
                f"{_tav_now.strftime('%Y-%m-%d')}_{_tav_now.hour:02d}"
                f"{(_tav_now.minute // 10) * 10:02d}"
            )
            _tav_df, _tav_rejim, _tav_chg, _tav_target = _service('_tav_compute_panel')(
                cache_key=_tav_ck
            )
            if _tav_df is not None and not _tav_df.empty:
                durum['tavan_adaylari_data'] = {
                    'df': _tav_df, 'rejim': _tav_rejim, 'xu_chg': _tav_chg,
                    'target_date': _tav_target,
                }
                if 'BIST' in _cat.upper():
                    _tav_log = _tav_df.copy()
                    _tav_log['Sembol'] = _tav_log['tk'].apply(
                        lambda value: value if '.IS' in str(value) else f'{value}.IS'
                    )
                    _tav_log['Fiyat'] = _tav_log['fiyat']
                    _tav_log['Skor'] = _tav_log['skor']
                    _tav_log['F_Tavan_Skor'] = _tav_log['skor']
                    _tav_log['F_Tavan_Kat'] = _tav_log['kat']
                    _tav_log['F_Tavan_Confluence'] = _tav_log['confluence_n']
                    _tav_alarm = _tav_log[_tav_log['skor'] >= 150]
                    if not _tav_alarm.empty:
                        _persist(durum, 'log_scan_signal', 'tavan_alarm', _tav_alarm, category=_cat)
                    _tav_top30 = _tav_log.head(30)
                    if not _tav_top30.empty:
                        _persist(durum, 'log_scan_signal', 'tavan_top30', _tav_top30, category=_cat)
                    _notify(
                        _state_bildir(durum), 'toast',
                        f"🚀 Tavan Motoru: {len(_tav_alarm)} alarm + {len(_tav_top30)} TOP30",
                    )
        except Exception as _tavan_exc:
            _record_error(durum, 'master_scan_tavan_motoru', _tavan_exc, _cat)
        return

    if step == 'flow_leaders':
        try:
            if 'BIST' in _cat.upper():
                _pal_df = _service('scan_para_akisi_liderleri')(_scan_list, _cat)
                if _pal_df is not None and not _pal_df.empty:
                    _persist(durum, 'log_scan_signal', 'para_akisi_lider', _pal_df, category=_cat)
                    _notify(
                        _state_bildir(durum), 'toast',
                        f'💧 Para Akışı Liderleri: {len(_pal_df)} hisse loglandı',
                    )
        except Exception as _pal_exc:
            _record_error(durum, 'master_scan_para_akisi', _pal_exc, _cat)
        return

    if step == 'stp_uyanis':
        try:
            durum['stp_uyanis_data'] = _service('scan_stp_uyanis_batch')(_scan_list)
        except Exception as _stp_exc:
            durum['stp_uyanis_data'] = pd.DataFrame()
            _record_error(durum, 'master_scan_stp_uyanis', _stp_exc, _cat)
        return

    if step == 'top20':
        durum['top_20_summary'] = _service('compile_top_20_summary')()
        durum['confluence_hits'] = _service('compile_confluence_hits')()
        return

    raise ValueError(f'Bilinmeyen Master Scan Faz 2 adımı: {step}')


def _state_bildir(durum: dict[str, Any]) -> Callable[[str, str], Any]:
    """Faz 2 adımlarındaki bildirimleri çağıranın kanalına yönlendirir."""
    bildir = durum.get('_ms_bildir')
    if not callable(bildir):
        raise RuntimeError('Master Scan Faz 2 bildirim kanalı bağlı değil')
    return bildir


def finalize_master_scan(durum: dict[str, Any], bildir: Callable[[str, str], Any],
                         progress: master_scan_progress.MasterScanProgress) -> None:
    """Faz 2 bitince eski kapanış işlerini aynı sonuçlarla tamamlar."""
    _cat = durum['category']
    _is_bist = bool(durum.get('is_bist'))
    _failures = durum.setdefault('persistence_failures', [])

    # Kuru koşu patron.db'ye veya tamamlanma kasasına dokunmaz.
    if not _dry_run(durum):
        try:
            if os.name == 'nt':
                _src = sqlite3.connect('patron.db')
                _dst = sqlite3.connect('patron_sync.db')
                _src.backup(_dst)
                _dst.close()
                _src.close()
                _sync_cmd = (
                    'scp -o StrictHostKeyChecking=no patron_sync.db '
                    'wm11tr@34.153.19.220:~/smr/patron.db.new && '
                    'ssh -o StrictHostKeyChecking=no wm11tr@34.153.19.220 '
                    '"mv ~/smr/patron.db.new ~/smr/patron.db"'
                )
                subprocess.Popen(
                    _sync_cmd, shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                _notify(bildir, 'toast', '🔄 patron.db VPS\'e gönderiliyor (tutarlı snapshot + atomik)')
        except Exception as _sync_exc:
            _record_error(durum, 'master_scan_patrondb_sync', _sync_exc, _cat)

    _er_df = durum.get('erken_radar_data')
    if _er_df is not None and not _er_df.empty:
        _er_primary_n = int((_er_df['Role'] == 'primary').sum())
        _notify(bildir, 'toast', f'🚀 Erken Radar: {_er_primary_n} hisse, {len(_er_df)} senaryo')
    else:
        _notify(bildir, 'toast', '⚠️ Erken Radar: Hiç senaryo eşleşmedi')
    _ms_write_frontend_preview(_cat, _er_df)
    _finish_progress(durum, bildir)

    _master_snapshot = {
        'ict_scan_data': durum['ict_scan_data'],
        'nadir_firsat_scan_data': durum['nadir_firsat_scan_data'],
        'golden_results': durum['golden_results'],
        'platin_results': durum['platin_results'],
        'tekli_altin_results': durum['tekli_altin_results'],
        'accum_data': durum['accum_data'],
        'scan_data': durum['scan_data'],
        'radar2_data': durum['radar2_data'],
        'liderlik_yolculugu_data': durum['liderlik_yolculugu_data'],
        'harmonic_confluence_data': durum['harmonic_confluence_data'],
        'minervini_data': durum['minervini_data'],
        'rs_leaders_data': durum.get('rs_leaders_data'),
        'guclu_donus_data': durum['guclu_donus_data'],
        'wilder_divergence_data': durum['wilder_divergence_data'],
        'stp_uyanis_data': durum.get('stp_uyanis_data'),
        'prelaunch_bos_data': durum['prelaunch_bos_data'],
        'top_20_summary': durum['top_20_summary'],
        'confluence_hits': durum['confluence_hits'],
        'golden_pattern_data': durum['golden_pattern_data'],
        'erken_radar_data': durum.get('erken_radar_data'),
        'formasyon_master_data': durum.get('formasyon_master_data'),
        'cizgi_yapi_master_data': durum.get('cizgi_yapi_master_data'),
        'magic_ribbon_session_data': durum.get('magic_ribbon_session_data'),
        'toplu_terazi_data': durum.get('toplu_terazi_data'),
    }
    _save_ok = False
    _skipped_keys: list[str] = []
    if not _dry_run(durum):
        try:
            pickle.dumps(_master_snapshot)
            _res = _service('save_scan_result')('master_scan', _master_snapshot, _cat)
            _save_ok = (_res is True)
        except Exception as _pickle_exc:
            logging.warning(
                '[scan_cache] Toplu pickle hatası: %s — key bazlı kayda geçiliyor',
                _pickle_exc,
            )
            _clean_snapshot = {}
            for _key, _value in _master_snapshot.items():
                try:
                    pickle.dumps(_value)
                    _clean_snapshot[_key] = _value
                except Exception as _key_exc:
                    _skipped_keys.append(_key)
                    logging.warning('[scan_cache] pickle edilemeyen key atlandı: %s — %s',
                                    _key, _key_exc)
            if _clean_snapshot:
                _res = _service('save_scan_result')('master_scan', _clean_snapshot, _cat)
                _save_ok = (_res is True)
    if _save_ok or _dry_run(durum):
        _notify(bildir, 'toast', '💾 Tarama sonuçları diske kaydedildi.')
    else:
        _skip_str = ', '.join(_skipped_keys[:5]) if _skipped_keys else 'bilinmiyor'
        _notify(bildir, 'warning', f'⚠️ Tarama önbelleği kaydedilemedi. Atlanan keyler: {_skip_str}')

    durum['generate_prompt'] = False
    durum['_master_scan_running'] = False
    if _is_bist and not _dry_run(durum):
        try:
            _daily = patron_db_guard.write_daily_karne(category=_cat)
            if not _daily.get('ok', False):
                _failures.append('Günlük Karne')
                _record_error(
                    durum, 'master_scan_daily_karne',
                    RuntimeError('; '.join(_daily.get('issues', []))), _cat,
                )
        except Exception as _daily_exc:
            _failures.append('Günlük Karne')
            _record_error(durum, 'master_scan_daily_karne', _daily_exc, _cat)
        _completion_saved = kapanis_master_otomasyon.mark_scan_completed(
            category=_cat, critical_failures=_failures,
        )
        if _completion_saved:
            durum['_kapanis_master_state'] = {
                'day': datetime.now(_service('_TZ_ISTANBUL')).strftime('%Y-%m-%d'),
                'started': True,
                'completed': not bool(_failures),
            }
            durum['_kapanis_master_auto_pending'] = False
            durum['_kapanis_master_auto_excluded'] = []
            if _failures:
                _notify(
                    bildir, 'warning',
                    '⚠️ Master Scan hesapları bitti ancak '
                    + ', '.join(_failures)
                    + " kaydı eksik kaldı. Günlük durum 'kısmi tamamlandı' olarak işaretlendi."
                )
            else:
                _notify(
                    bildir, 'toast',
                    'Bugünkü BIST Master Scan tamamlandı. Otomatik kontrol sonraki işlem günü 20:00\'de yapılacak.',
                )
        else:
            _notify(
                bildir, 'warning',
                '⚠️ Master Scan bitti ancak günlük tamamlanma kaydı yazılamadı; '
                'otomatik tekrar güvenlik için kapalı değil.',
            )

    durum['_ms_faz2_bekliyor'] = []
    durum['_ms_faz2_baglam'] = {}
    durum['_ms_faz2_resume_once'] = False
    durum['_ms_faz2_interruptions'] = 0
    durum.pop('_ms_progress', None)
    durum.pop('_ms_progress_value', None)
    durum.pop('_ms_bildir', None)
    _notify(bildir, 'clear', '')


def execute_pending_phase2(durum: dict[str, Any],
                           bildir: Callable[[str, str], Any]) -> bool:
    """Ekran çizildikten sonra kalan Faz 2 adımlarını sırayla tamamlar."""
    _pending = list(durum.get('_ms_faz2_bekliyor') or [])
    if not _pending:
        return False
    _ctx = durum.get('_ms_faz2_baglam') or {}
    _progress_steps = list(_ctx.get('progress_steps') or (_MS_PHASE1_STEPS + _pending))
    _progress = durum.get('_ms_progress')
    if (
        not isinstance(_progress, master_scan_progress.MasterScanProgress)
        or _progress.category != str(_ctx.get('category', '')).strip()
        or tuple(_progress_steps) != tuple(_progress.steps)
    ):
        _progress = master_scan_progress.MasterScanProgress(
            _ctx.get('category', ''), _progress_steps,
        )
        _progress.completed.update(set(_progress_steps) - set(_pending))
        durum['_ms_progress'] = _progress
    _ctx['category'] = _ctx.get('category', durum.get('category', ''))
    durum['category'] = _ctx['category']
    durum['is_bist'] = bool(_ctx.get('is_bist'))
    _ctx['persistence_failures'] = list(_ctx.get('persistence_failures') or [])
    _ctx['progress_steps'] = _progress_steps
    durum['persistence_failures'] = _ctx['persistence_failures']
    durum['_ms_bildir'] = bildir
    try:
        for _step in _pending:
            _label = _MS_PHASE2_LABELS.get(_step, _step)
            _begin_progress(durum, bildir, _step, _label)
            run_phase2_step(_step, durum, _ctx)
            _pending = [key for key in _pending if key != _step]
            durum['_ms_faz2_bekliyor'] = _pending
        _ctx['persistence_failures'] = list(_ctx.get('persistence_failures') or [])
        durum['persistence_failures'] = _ctx['persistence_failures']
        finalize_master_scan(durum, bildir, _progress)
        return True
    except Exception as _phase2_exc:
        durum['_master_scan_running'] = False
        _release_scan_start(durum)
        _record_error(durum, 'master_scan_phase2', _phase2_exc,
                      str(_ctx.get('category', '')))
        _notify(bildir, 'error', f'Faz 2 sırasında bir hata oluştu: {str(_phase2_exc)}')
        return False
