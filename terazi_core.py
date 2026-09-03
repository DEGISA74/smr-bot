"""
terazi_core — KANIT TERAZİSİ saf hesap çekirdeği (17 Tem 2026, EKRAN REFORMU 1a)

Ekranın TEK sentez hükmünü üretir: boğa/ayı oyları toplar, hüküm + güven + çelişki
+ "en güçlü karşı sinyal" satırını döner. Render app.py'de (kural: render'lar app.py).

İLKELER (memory/project_ekran_hiyerarsi_reformu.md):
  - HAM SİNYAL = OY, TÜREV SKOR = LENS: Yol Haritası/Teknik/Eğilim gibi bileşik
    skorlar buraya OY olarak GİREMEZ (aynı girdinin çift sayımı = sözde-teyit).
  - İki sınıf oy: ölçülmüş (karneli, tam çizim) / ölçülmemiş (yarım ağırlık, soluk ◐).
  - Yön simetrisi: karşı-sinyal satırı boğa/ayı iki yönde de aynı kurala tabidir.

Oy kaynakları ve ağırlık kanıtları:
  - RSI kovaları     → rsi_kova_backtest.py (17 Tem 2026, 604 hisse 133K olay):
      hisse RSI<25: 10g ort ~+%2.1 isabet %57-60 (referans %48.5) → TAM boğa oyu
      endeks RSI<25: 10g ort +%5 isabet %86-90 (N=34, az örnek)  → TAM boğa oyu
      RSI>80: 10g ort +%3.4 AMA medyan +%0.5 (çarpık, isabet %51.6)
              → YARIM boğa oyu (momentum devamı; "aşırı alım-düşer" anlatısı YASAK)
      70-80: referanstan ayrışmıyor → OY YOK
  - CMF dual         → universe_snapshot: CMF tek rejim-dayanıklı sinyal;
      turning_up = 56K backtest'te EN ZAYIF kova (tarihsel tuzak) → YARIM AYI oyu
  - Yabancı streak   → feature_karne: streak gerçek edge (+3.2); tek-gün TERS → sadece streak oylar
  - SFP              → 17 Tem'den beri loglanıyor, karnesi Eylül'de → ÖLÇÜLMEMİŞ yarım oy
  - ICT bias         → f_ict_model karnesi Eylül'de → ÖLÇÜLMEMİŞ yarım oy (4 Tem emsali)
  - Tarama üyelikleri/ER red-flag/harmonik/fırsat-fail/formasyon → app.py toplar (mevcut kaynaklar)
"""


def mk_vote(ad, yon, agirlik, olculmus, neden, prio=5, family=None):
    """Tek oy. yon: 'boga'|'ayi' · olculmus: karneli mi · prio: karşı-sinyal adaylığı (>=8 manşetlik).
    family: aynı ham kaynaktan türeyen oyları grup olarak tavanlamak için (build_terazi
    FAMILY_CAPS okur). None = bağımsız oy. 'akis_destek' = fiyat-hacim ailesinin DESTEK
    üyeleri (OBV/MFI/hacim/rel_OBV/UDVR) — birlikte net en fazla ±0.5 sayılır (27 Tem 2026,
    borsacı geri bildirimi rec 2: aynı günlük fiyat-hacim haberini 5 kez saymayı bırak).
    CMF aileye GİRMEZ — ölçülmüş, rejim-dayanıklı güçlü standalone (tam bağımsız 1.0)."""
    return {'ad': ad, 'yon': yon, 'agirlik': float(agirlik),
            'olculmus': bool(olculmus), 'neden': str(neden), 'prio': int(prio),
            'family': family}


def votes_from_features(feat, is_index=False):
    """_compute_signal_features çıktısından ham-sinyal oyları üretir (tek kaynak: scan_pipeline).
    Türev skorlar (f_master_score, f_sentiment_score...) BİLEREK kullanılmaz — ilke 2."""
    v = []
    if not isinstance(feat, dict):
        return v
    # ── RSI uç bölgeleri (karne: rsi_kova_backtest 17 Tem 2026) ──
    rsi = feat.get('f_rsi')
    if rsi is not None:
        try:
            rsi = float(rsi)
            if rsi < 25:
                karne = ("◆ endekste benzer günlerin 10'da ~9'u kazandırdı (az örnek)"
                         if is_index else
                         "◆ benzer günlerin 10'da ~6'sı kazandırdı (10 günde ort +%2)")
                v.append(mk_vote("RSI uç aşırı satım", 'boga', 1.0, True,
                                 f"RSI {rsi:.0f} — uç aşırı satım {karne}", prio=8))
            elif rsi > 80:
                v.append(mk_vote("RSI momentum ucu", 'boga', 0.5, True,
                                 f"RSI {rsi:.0f} — momentum ucu ◆ ortalamada kazandırdı ama "
                                 f"kazanç az sayıda hissede toplandı", prio=6))
        except Exception:
            pass
    # ── CMF dual state (karne: rejim-dayanıklı; turning_up = tarihsel tuzak) ──
    # 18 Tem akşam: neden metinleri 2 cümleye çıkarıldı (kullanıcı: "anlatımlar kısır").
    # NOT (18 Tem akşam geri alma): endeks CMF oyu DÜŞÜRÜLMÜYOR — XU100 gerçek TL hacme
    # kavuştu, hisse gibi tam hacim analizi ister (kullanıcı: "endekste de hisse paneli").
    cmf = feat.get('f_cmf_dual')
    if cmf == 'strong_pos':
        v.append(mk_vote("Para akışı güçlü pozitif", 'boga', 1.0, True,
                         "Para akışı (CMF) hem 5 hem 20 günlük pencerede pozitif. "
                         "Tek günlük heves değil, kararlı bir para girişi görüntüsü", prio=7))
    elif cmf == 'strong_neg':
        v.append(mk_vote("Para akışı güçlü negatif", 'ayi', 1.0, True,
                         "Para akışı (CMF) hem 5 hem 20 günlük pencerede negatif. "
                         "Tek günlük ürküntü değil, kararlı bir para çıkışı görüntüsü", prio=7))
    elif cmf == 'turning_up':
        v.append(mk_vote("Para akışı tuzak görüntüsü", 'ayi', 0.5, True,
                         "5 günlük akış pozitife döndü ama 20 günlük hâlâ negatif "
                         "◆ geçmişte bu görüntü çoğunlukla ölü kedi sıçraması çıktı", prio=6))
    elif cmf == 'pos':
        v.append(mk_vote("Para akışı pozitif", 'boga', 0.5, True,
                         "20 günlük para akışı (CMF) pozitif — uzun pencerede alıcılar önde. "
                         "Kısa pencere henüz teyit vermedi, o yüzden yarım oy", prio=4))
    elif cmf == 'neg':
        v.append(mk_vote("Para akışı negatif", 'ayi', 0.5, True,
                         "20 günlük para akışı (CMF) negatif — uzun pencerede satıcılar önde. "
                         "Kısa pencere henüz teyit vermedi, o yüzden yarım oy", prio=4))
    # ── Yabancı streak (karne: streak gerçek edge; tek-gün TERS olduğundan oylamaz) ──
    try:
        if int(feat.get('f_yabanci_streak') or 0) >= 1:
            v.append(mk_vote("Yabancı 3+ gün alıcı", 'boga', 1.0, True,
                             "Yabancı yatırımcı payı 3 günden uzun süredir üst üste artıyor. "
                             "◆ Geçmiş ölçümde tek günlük alım değil, bu tür seriler gerçek "
                             "avantaj gösterdi", prio=6))
    except Exception:
        pass
    # ── SFP tuzakları (ölçülmemiş — Eylül karnesine kadar yarım oy) ──
    if feat.get('f_sfp_bull'):
        v.append(mk_vote("Bullish SFP (ayı tuzağı)", 'boga', 0.5, False,
                         "Fiyat dip seviyeyi fitille süpürüp üstünde kapattı — satıcıları "
                         "tuzağa düşüren görüntü. Karnesi henüz ölçülmedi (Eylül)", prio=9))
    if feat.get('f_sfp_bear'):
        v.append(mk_vote("Bearish SFP (boğa tuzağı)", 'ayi', 0.5, False,
                         "Fiyat tepe seviyeyi fitille süpürüp altında kapattı — alıcıları "
                         "tuzağa düşüren görüntü. Karnesi henüz ölçülmedi (Eylül)", prio=9))
    return v


def semsiye_votes(scenario_ids):
    """1b ŞEMSİYE ROZETİ (18 Tem 2026) — er_D4/er_D5 ölçülmüş DÜŞÜŞ uyarıları.
    Temiz DB karnesi (18 Tem harita revizyonu ölçümü):
      er_D4 (Kurumsal Satış Riski): 10g isabet %65 (N=994) · 20g %65.6, ort -%2.9 (N=675)
      er_D5 (Trend Bozuldu):        10g isabet %61 (N=380) · 20g %66.4, ort -%4.4 (N=241)
    Boğa taramaları çökerken uyarı olarak ayakta kalan TEK ölçülmüş çift — Dönüş El
    Kitabı kuralı: 'er_D4 yanarken alma'. Boğa tier haritasına GİRMEZLER (long olarak
    negatif); buraya AYI oyu + kart rozeti olarak akarlar (ilke 3: bilgi yukarı akar,
    ilke 7: yön simetrisi). prio=8 → hüküm yukarıyken karşı-sinyal satırına çıkabilir.
    Döner: oy listesi; her oyda ekstra 'semsiye': True (render rozet bandı bundan tanır)."""
    _K = {
        'D4': ("Şemsiye: kurumsal satış izi",
               "Son 5 günde 2+ hacimli kırmızı kapanış — kurumsal satış izi "
               "◆ benzer günlerin 10'da 6,5'i sonraki 20 günde düşürdü (ort -%3)"),
        'D5': ("Şemsiye: trend bozuldu",
               "50 günlük ortalama altı + hacimli kırmızı gün + endekse karşı zayıf "
               "◆ benzer günlerin 10'da 6,5'i sonraki 20 günde düşürdü (ort -%4)"),
    }
    v = []
    for sid in (scenario_ids or []):
        key = str(sid).upper().replace('ER_', '')
        if key in _K:
            ad, neden = _K[key]
            oy = mk_vote(ad, 'ayi', 1.0, True, neden, prio=8)
            oy['semsiye'] = True
            v.append(oy)
    return v


def dokum_ozeti(factors, comp_score=None, esik=40):
    """3c REFORM (17 Tem 2026) — skor iç dökümü kuralı: HEMFİKİRSE SUS, ÇELİŞİYORSA KONUŞ.
    factors: {görünen_ad: 0-100 | None} (None = ölçülemedi — sahte-nötr 50 YASAK, ilke 1).
    Bileşenler ayrışıksa (max-min ≥ esik) çıplak sayı listesi yerine TEK CÜMLE üretir;
    uyumluysa döküm gizlenir (sayılar tooltip'te yaşar).
    Döner: {'mod': 'uyumlu'|'ayrisik'|'yetersiz', 'cumle': str|None, 'tooltip': str, 'eksik': [adlar]}"""
    factors = factors or {}
    eksik = [k for k, v in factors.items() if v is None]
    valid = {}
    for k, v in factors.items():
        if v is None:
            continue
        try:
            valid[k] = max(0.0, min(100.0, float(v)))
        except Exception:
            eksik.append(k)
    tt = " · ".join(f"{k} {v:.0f}" for k, v in valid.items())
    if eksik:
        tt += (" · " if tt else "") + "ölçülemedi: " + ", ".join(eksik)
    if len(valid) < 2:
        return {'mod': 'yetersiz', 'cumle': None, 'tooltip': tt, 'eksik': eksik}
    vmax, vmin = max(valid.values()), min(valid.values())
    if (vmax - vmin) < esik:
        return {'mod': 'uyumlu', 'cumle': None, 'tooltip': tt, 'eksik': eksik}
    strongs = [k for k, v in valid.items() if v >= 60] or [max(valid, key=valid.get)]
    weaks = [k for k, v in valid.items() if v <= 40] or [min(valid, key=valid.get)]
    if comp_score is not None and comp_score < 45:
        son = "düşük skorun sebebi bu"
    elif comp_score is not None and comp_score >= 65:
        son = "yüksek skora rağmen zayıf halka var"
    else:
        son = "skorun ortada kalma sebebi bu"
    cumle = (f"Bileşenler ayrışık: {', '.join(strongs)} sağlam ama "
             f"{', '.join(weaks)} zayıf — {son}.")
    return {'mod': 'ayrisik', 'cumle': cumle, 'tooltip': tt, 'eksik': eksik}


def votes_from_genel_ozet(sigs, is_index=False):
    """3b AŞAMA 3 (17 Tem 2026) — GENEL ÖZET 6-oy sisteminin RSI/CMF DIŞI üyeleri
    teraziye bağlanır (V8 sistemi 56K örnekle ölçüldü → üyeler ölçülmüş sayılır, 0.5 oy).
    RSI + CMF BİLEREK ALINMAZ: terazide zaten uç-bölge/dual-state oyları var —
    aynı ham sinyalin çift sayımı sözde-teyit üretir (kalıcı ilke 2).
    sigs: {'hacim': ±1|0, 'obv': ±1|0, 'yapi': ±1|0, 'mfi': ±1|0}
    NOT (18 Tem akşam geri alma): endeks hacim/OBV/MFI oyları DÜŞÜRÜLMÜYOR — XU100 gerçek
    TL hacme kavuştu, tam hacim analizi ister (is_index param tutuldu ama filtre yapmaz)."""
    # 18 Tem akşam: neden metinleri 2 cümleye çıkarıldı (satırda tam gerekçe görünüyor).
    _MAP = {
        'hacim': ("Alım hacmi baskın", "Satış hacmi baskın",
                  "Son 5 günün işlem hacmi ağırlıkla alıcı tarafında geçti. "
                  "Satıcılar aynı istekle karşılık vermiyor",
                  "Son 5 günün işlem hacmi ağırlıkla satıcı tarafında geçti. "
                  "Alıcılar aynı istekle karşılık vermiyor"),
        'obv':   ("OBV birikim izi", "OBV dağıtım izi",
                  "OBV yükseliyor — fiyat sakinken hacim alıcı yönde birikiyor. "
                  "Sessiz birikim izi",
                  "OBV düşüyor — hacim satıcı yönde akıyor. Sessiz dağıtım izi"),
        'yapi':  ("Yapı yükseliyor", "Yapı bozuluyor",
                  "Fiyat yapısı yükselen: dipler bir öncekinin üstünde oluşuyor. "
                  "Alıcılar her geri çekilmede daha erken giriyor",
                  "Fiyat yapısı alçalan: tepeler bir öncekinin altında kalıyor. "
                  "Satıcılar her tepkide daha erken devreye giriyor"),
        'mfi':   ("MFI aşırı satım", "MFI erken aşırı alım",
                  "MFI hem kısa hem uzun pencerede aşırı satım bölgesinde. "
                  "Geçmişte bu bölge toparlanma alanı olarak çalıştı",
                  "MFI kısa pencerede erken aşırı alıma girdi. Momentum sürebilir "
                  "ama soğuma riski artık masada"),
    }
    # hacim/OBV/MFI aynı fiyat-hacimden türer → 'akis_destek' ailesi (birlikte tavanlanır).
    # yapı (market structure) ayrı kalır — fiyat yapısı, hacim ailesi değil.
    _AKIS = {'hacim', 'obv', 'mfi'}
    v = []
    for k, (ad_b, ad_a, ned_b, ned_a) in _MAP.items():
        try:
            s = int((sigs or {}).get(k) or 0)
        except Exception:
            s = 0
        _fam = 'akis_destek' if k in _AKIS else None
        if s > 0:
            v.append(mk_vote(ad_b, 'boga', 0.5, True, ned_b, prio=5, family=_fam))
        elif s < 0:
            v.append(mk_vote(ad_a, 'ayi', 0.5, True, ned_a, prio=5, family=_fam))
    return v


def votes_from_smart_flow(rel_state=None, udvr_state=None):
    """23 Tem 2026 — GENEL ÖZET panelinde GÖRÜNEN ama terazinin GÖRMEDİĞİ iki
    akıllı-para sinyali. Kullanıcı çelişkiyi yakaladı: panel "Güçlü Alıcı 2.0x"
    ve "Endeksten Sıyrılıyor" derken terazi "karşı oy yok" diyordu — kördü.

    Panelle AYNI fonksiyonlardan gelen state'ler bağlanır (tek kaynak):
      rel_state  ← compute_relative_obv_state (hisse OBV vs endeks)
      udvr_state ← compute_updown_volume_ratio (yükseliş/düşüş günü hacmi)

    Ağırlık 0.5 — kardeş panel oylarıyla aynı, sezgisel değil eş-seviye. (Bugünkü
    split-half testi bireysel ağırlık vermenin erken olduğunu gösterdi; bu yüzden
    ölçülen katkıya göre AĞIRLIKLANDIRILMADI, sadece EKSİK OY olarak eklendi.)"""
    v = []
    _REL = {
        'outperform_strong': ('boga', "Endeksten sıyrılıyor",
                              "Hisse hacim akışı pozitif, endeks negatif — akıllı para bu hisseyi seçti"),
        'outperform_mild':   ('boga', "Endeksten hızlı",
                              "Hacim akışı endeksten güçlü, göreli avantaj var"),
        'underperform_strong': ('ayi', "Endekse göre çıkış izi",
                              "Hisse hacim akışı negatif, endeks pozitif — yapısal çıkış izi"),
        'underperform_mild': ('ayi', "Endekse göre zayıf",
                              "Hacim akışı endeksten geride"),
    }
    if rel_state in _REL:
        yon, ad, ned = _REL[rel_state]
        v.append(mk_vote(ad, yon, 0.5, True, ned, prio=5, family='akis_destek'))

    _UDVR = {
        'climax_bottom': ('boga', "Dip climax — alıcı güçlü",
                          "Fiyat dipte ama alıcı hacmi güçlü — smart money giriyor olabilir"),
        'strong_buyer':  ('boga', "Güçlü alıcı hakim",
                          "Yükseliş günü hacmi düşüş gününün katı — alıcı net baskın"),
        'buyer':         ('boga', "Alıcı egemen",
                          "Yükseliş günü hacmi daha fazla — sağlıklı yukarı baskı"),
        'climax_top':    ('ayi', "Tepe climax — alıcı çekiliyor",
                          "Fiyat tepede ama hacim alıcı yönde değil — smart money çekiliyor olabilir"),
        'strong_seller': ('ayi', "Güçlü satıcı hakim",
                          "Düşüş günü hacmi yükseliş gününün katı — satıcı net baskın"),
        'seller':        ('ayi', "Satıcı egemen",
                          "Düşüş günü hacmi daha fazla — yukarı baskı zayıf"),
    }
    if udvr_state in _UDVR:
        yon, ad, ned = _UDVR[udvr_state]
        v.append(mk_vote(ad, yon, 0.5, True, ned, prio=5, family='akis_destek'))
    return v


def rsi_uc_rozeti(close, is_index=False):
    """2b REFORM (17 Tem 2026) — FİYAT kartı RSI hücresinin uç modu.
    Eşikler rsi_kova_backtest ölçümünden: <25 (kanıtlı boğa bölgesi) / >80 (momentum ucu).
    70-80 BİLEREK rozetsiz — referanstan ayrışmadı. RSI formülü f_rsi/backtest ile aynı
    (SMA-14; kartın kendi RSI'ından ondalık sapabilir — rozet kendi değerini yazar).
    Döner: dict {'mod','rsi','gun','label','karne','renk'} | None (normal mod, 25-80)."""
    try:
        if close is None or len(close) < 20:
            return None
        c = close.astype(float)
        delta = c.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, float('nan'))
        rsi = (100 - 100 / (1 + rs)).where(~(loss == 0), 100.0)
        v = float(rsi.iloc[-1])
        if v != v or (25.0 <= v <= 80.0):
            return None
        arr = rsi.to_numpy()
        gun = 0
        for i in range(len(arr) - 1, -1, -1):
            x = arr[i]
            if x != x:
                break
            if (v < 25.0 and x < 25.0) or (v > 80.0 and x > 80.0):
                gun += 1
            else:
                break
        if v < 25.0:
            sure = "bugün 25 altına indi" if gun <= 1 else f"{gun} gündür 25 altında"
            karne = ("◆ endekste benzer günlerin 10'da ~9'u kazandırdı (az örnek)"
                     if is_index else
                     "◆ benzer günlerin 10'da ~6'sı kazandırdı (10 günde ort +%2)")
            return {'mod': 'dip', 'rsi': v, 'gun': gun, 'renk': '#f59e0b',
                    'label': f"RSI {v:.0f} — UÇ AŞIRI SATIM · {sure}", 'karne': karne}
        sure = "bugün 80 üstüne çıktı" if gun <= 1 else f"{gun} gündür 80 üstünde"
        karne = ("◆ endekste benzer günler çoğunlukla devam etti (az örnek)"
                 if is_index else
                 "◆ geçmişte ortalamada kazandırdı ama kazanç az sayıda hissede toplandı")
        return {'mod': 'tepe', 'rsi': v, 'gun': gun, 'renk': '#a78bfa',
                'label': f"RSI {v:.0f} — MOMENTUM UCU · {sure}", 'karne': karne}
    except Exception:
        return None


def gun_karakteri(df):
    """Son işlem gününün karakteri: getiri % + kapanış konumu (0=dip, 1=tepe) + hacim çarpanı.
    Tanımlar sert_gun_backtest.py ile BİREBİR AYNI (vol20 bugünü içerir) — karne aynı dili konuşsun."""
    try:
        if df is None or len(df) < 21:
            return None
        c = df['Close'].astype(float); h = df['High'].astype(float)
        l = df['Low'].astype(float); v = df['Volume'].astype(float)
        ret1 = float(c.iloc[-1] / c.iloc[-2] - 1.0) * 100.0
        rng = float(h.iloc[-1] - l.iloc[-1])
        loc = float((c.iloc[-1] - l.iloc[-1]) / rng) if rng > 0 else 0.5
        v20 = float(v.rolling(20).mean().iloc[-1])
        vmult = float(v.iloc[-1] / v20) if v20 > 0 else 0.0
        return {'ret1': ret1, 'close_loc': loc, 'vol_mult': vmult}
    except Exception:
        return None


def sok_degerlendir(char, is_index=False):
    """ŞOK PAKETİ (17 Tem 2026, sert_gun_backtest kanıtı — 604 hisse, 15.841 şok günü):
      - Hisse ≤ -%3 + hacim ≥1.5x → ÖLÇÜLMÜŞ AYI OYU (hacimli şok: 10g ort -%1.8, isabet %40;
        çok sert ≤-%5 + dipte kapanış + hacimli: 10g -%3.2, isabet %36 — kurumsal dağıtım imzası).
      - Hisse ≤ -%3 + hacim normal → OY YOK, sadece şerit (ölçüm: yön öngörmüyor, 10g +%1.4 = referans).
      - Yukarı şok (≥ +%3) → şerit (yön iddiasız tazelik notu; yukarı-şok karnesi ölçülmedi).
      - Endeks (|%2|+) → SADECE şerit (N=70, kanıt yetersiz — oy yazılmaz).
    Döner: (votes, serit_text|None). Oy varsa şerit verilmez (çift konuşma olmasın)."""
    if not char:
        return [], None
    r, loc, vm = char['ret1'], char['close_loc'], char['vol_mult']
    if is_index:
        if r <= -2.0:
            return [], (f"Bugün {r:+.1f}% — endekste sert gün. Buradaki kanıtlar yavaş "
                        f"sinyaller; bugünü henüz sindirmemiş olabilir.")
        if r >= 2.0:
            return [], (f"Bugün {r:+.1f}% — endekste sert yukarı gün. Buradaki kanıtlar "
                        f"bugünü henüz sindirmemiş olabilir.")
        return [], None
    if r <= -3.0:
        if vm >= 1.5:
            if r <= -5.0 and loc <= 0.35:
                neden = (f"Bugün {r:+.1f}% + {vm:.1f}x hacim + dipte kapanış — kurumsal satış "
                         f"imzası ◆ benzer günlerden sonra düşüş çoğunlukla sürdü (10 günde ort -%3)")
            else:
                neden = (f"Bugün {r:+.1f}% + {vm:.1f}x hacim ◆ benzer günlerden sonra "
                         f"düşüş çoğunlukla sürdü (10 günde ort -%2)")
            return [mk_vote("Hacimli sert düşüş", 'ayi', 1.0, True, neden, prio=8)], None
        return [], (f"Bugün {r:+.1f}% sert hareket ama hacim normal ({vm:.1f}x). Geçmişte böyle "
                    f"günler yön belirlemedi — sonraki 10 gün sıradan günlerden farksız çıktı.")
    if r >= 3.0:
        return [], (f"Bugün {r:+.1f}% sert yukarı hareket. Buradaki kanıtlar yavaş sinyaller; "
                    f"bugünü henüz sindirmemiş olabilir.")
    return [], None


def sistemik_gun(neg_oran, medyan):
    """1c PİYASA-ŞOKU MODU (18 Tem 2026) — sistemik gün tespiti, SAF karar.
    Eşikler ÖLÇÜMDEN (sistemik_gun_olcum, 626 hisse × 266 gün, son 15 ay):
      neg_oran ≥ %80 VE medyan ≤ -%2.0 → 15 ayda 9 gün (≈p97-98) — bilinen şok
      günleriyle birebir örtüştü (21 May 2026: %94 kırmızı/medyan -6.1 · 19 Şub · 2 Mar).
      Daha gevşek aday (%75/-1.5) 21 gün yakalıyordu = askı için fazla sık.
    Askı bir ÖLÇÜM İDDİASI DEĞİL tasarım sigortasıdır (sistemik günde hisse-özel kanıt
    okunmaz); şerit dili yön iddiasız. AI prompt'a GİRMEZ (rejim yasağı — UI seviyesi).
    Döner: {'aktif': bool, 'serit': str|None}"""
    try:
        if neg_oran is None or medyan is None:
            return {'aktif': False, 'serit': None}
        if float(neg_oran) >= 80.0 and float(medyan) <= -2.0:
            return {'aktif': True,
                    'serit': (f"Bugün piyasa geneli şok günü: hisselerin %{float(neg_oran):.0f}'i "
                              f"düştü, piyasa medyanı {float(medyan):+.1f}%. Böyle genişlikte günler "
                              f"son 15 ayda 9 kez görüldü — bugün düşüş hisseye özel değil, piyasa "
                              f"geneli. Hisse kanıtları bu gürültüde ayırt edici olmaz; hüküm askıda.")}
    except Exception:
        pass
    return {'aktif': False, 'serit': None}


def build_terazi(votes, is_index=False):
    """Oy listesinden hüküm üretir.
    Döner: {'yon','guven','celiski','tek_sinyal','boga_w','ayi_w','votes','karsi','endeks_notu'}"""
    votes = [x for x in (votes or []) if x and x.get('agirlik', 0) > 0]
    # AİLE TAVANI (27 Tem 2026, rec 2): aynı ham kaynaktan türeyen oylar (fiyat-hacim
    # destek ailesi) bağımsız kanıt sayılmaz — grup net'i tavanla sınırlanır. Bireysel
    # oylar LİSTEDE kalır (şeffaflık), sadece verdict ağırlığı tavanlanır. CMF aile DIŞI
    # → tam bağımsız 1.0 (opsiyon B). n_votes hâlâ bireysel oy sayar.
    FAMILY_CAPS = {'akis_destek': 0.5}
    boga_w = 0.0; ayi_w = 0.0
    fam_net = {}
    for x in votes:
        _fam = x.get('family')
        if _fam in FAMILY_CAPS:
            _signed = x['agirlik'] if x['yon'] == 'boga' else -x['agirlik']
            fam_net[_fam] = fam_net.get(_fam, 0.0) + _signed
        elif x['yon'] == 'boga':
            boga_w += x['agirlik']
        else:
            ayi_w += x['agirlik']
    for _fam, _net in fam_net.items():
        _cap = FAMILY_CAPS[_fam]
        _capped = max(-_cap, min(_cap, _net))
        if _capped > 0:
            boga_w += _capped
        elif _capped < 0:
            ayi_w += -_capped
    boga_w = round(boga_w, 1); ayi_w = round(ayi_w, 1)
    n = len(votes)
    out = {'yon': 'dengede', 'guven': None, 'celiski': False, 'tek_sinyal': False,
           'boga_w': boga_w, 'ayi_w': ayi_w, 'n_votes': n,
           'votes': sorted(votes, key=lambda x: (-x['prio'], -x['agirlik'])),
           'karsi': None,
           'endeks_notu': ("Endekste tarama kanıtı yapısal olarak olmaz — "
                           "terazi gösterge sinyalleriyle kuruldu." if is_index else "")}
    if n == 0:
        return out
    fark = boga_w - ayi_w
    if abs(fark) >= 0.5:
        out['yon'] = 'yukari' if fark > 0 else 'asagi'
    # 1 — tek oydan yüzde üretilmez (4 Tem kuralı korunur)
    if n >= 2 and (boga_w + ayi_w) > 0:
        out['guven'] = int(max(boga_w, ayi_w) / (boga_w + ayi_w) * 100)
        out['celiski'] = (boga_w > 0 and ayi_w > 0)
    elif n == 1:
        out['tek_sinyal'] = True
    # 2c KARARI — zorunlu, yön-simetrik "en güçlü karşı sinyal" satırı (prio ≥ 8)
    if out['yon'] in ('yukari', 'asagi'):
        loser = 'ayi' if out['yon'] == 'yukari' else 'boga'
        cands = [x for x in votes if x['yon'] == loser and x['prio'] >= 8]
        if cands:
            out['karsi'] = max(cands, key=lambda x: (x['prio'], x['agirlik']))
    return out


def yon_label(yon):
    return {'yukari': ('YUKARI AĞIR BASIYOR', '#22c55e'),
            'asagi': ('AŞAĞI AĞIR BASIYOR', '#f87171')}.get(yon, ('DENGEDE', '#94a3b8'))


def _risk_odul_dis_seviye(df, price):
    """VA dışındaki taraf hesabı için mevcut kısa vadeli seviyeleri çıkarır."""
    try:
        if df is None or len(df) < 5:
            return None
        _high = [float(x) for x in df['High'].iloc[-21:].tolist()]
        _low = [float(x) for x in df['Low'].iloc[-21:].tolist()]
        if not _high or not _low:
            return None
        _raw_low5 = min(_low[-5:])
        _raw_high5 = max(_high[-5:])
        _ranges = [h - l for h, l in zip(_high[-14:], _low[-14:]) if h > l]
        _atr = (sum(_ranges) / len(_ranges)) if _ranges else price * 0.03
        if _atr <= 0:
            return None
        # GENEL ÖZET'teki 5 günlük stop/iptal seviyeleriyle aynı sınırlar.
        _low5 = max(_raw_low5, price - 2.0 * _atr)
        _high5 = min(_raw_high5, price + 2.0 * _atr)
        _prior_highs = [x for x in _high[:-1] if x > price]
        _prior_lows = [x for x in _low[:-1] if x < price]
        return {
            'low5': _low5 if _low5 < price else None,
            'high5': _high5 if _high5 > price else None,
            'prior_resistance': min(_prior_highs) if _prior_highs else None,
            'prior_support': max(_prior_lows) if _prior_lows else None,
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def risk_odul_kapisi(price, vah, val, va_pos, r_iyi=1.5, r_kotu=1.0,
                     yon=None, df=None):
    """GÜVENLİK KAPISI — yönlü risk-ödül geometrisi (21 Tem 2026).

    Soru: seçilen yönde mevcut fiyat ile geçerli hedef arasındaki kazanç alanı,
    geçersizlik/stop seviyesine kadar olan riske göre yeterli mi?

    Fiyat Değer Alanı (VA) içindeyse yakın seviyeler VAH/VAL'dir. Fiyat VA
    dışındaysa hesap susmaz: aşağıda kalan fiyat için ilk VA sınırı ve mevcut
    kısa vadeli dip/tepe, yukarıda kalan fiyat için ilk VA sınırı ve kısa vadeli
    tepe/dip kullanılır. Böylece Long ve Short aynı geometri kuralını paylaşır.

    `yon` 'yukari' ise Long, 'asagi' ise Short hesabıdır. Yön veya geçerli
    hedef/stop seviyesi yoksa None döner; sayı üretilemeyen yerde ölçülmüş gibi
    davranılmaz. R = kazanç mesafesi / risk mesafesi.

    Dönüş: {'seviye': 'kirmizi'|'amber'|None, 'r': float, 'metin': str} veya None."""
    try:
        price = float(price); vah = float(vah); val = float(val)
    except (TypeError, ValueError):
        return None
    _yon = str(yon or '').strip().lower()
    if _yon not in ('yukari', 'asagi') or price <= 0 or not (val < vah):
        return None

    _inside = val < price < vah
    _levels = _risk_odul_dis_seviye(df, price) if not _inside else None
    if _inside:
        _target = vah if _yon == 'yukari' else val
        _stop = val if _yon == 'yukari' else vah
    elif price < val:
        if not _levels:
            return None
        if _yon == 'yukari':
            # VA altındaki Long için ilk hedef VAL, risk kısa vadeli dip.
            _target = val
            _stop = _levels.get('low5')
        else:
            # VA altındaki Short için hedef fiyatın altındaki son destek,
            # geçersizlik ise VAL'ın geri alınmasıdır.
            _target = _levels.get('prior_support')
            _stop = val
    elif price > vah:
        if not _levels:
            return None
        if _yon == 'yukari':
            # VA üstündeki Long için hedef, fiyatın üstündeki ilk tarihsel direnç;
            # destek olarak önce VAH kullanılır.
            _target = _levels.get('prior_resistance')
            _stop = vah
        else:
            # VA üstündeki Short için ilk hedef VAH, risk kısa vadeli tepe.
            _target = vah
            _stop = _levels.get('high5')
    else:
        return None

    if _target is None or _stop is None:
        return None
    _kazanc = (_target - price) if _yon == 'yukari' else (price - _target)
    _risk = (price - _stop) if _yon == 'yukari' else (_stop - price)
    if _kazanc <= 0 or _risk <= 0:
        return None
    _r = _kazanc / _risk
    _kazanc_pct = _kazanc / price * 100
    _risk_pct = _risk / price * 100
    _taraf = 'Long' if _yon == 'yukari' else 'Short'
    _base = {'r': round(_r, 2),
             'kazanc_pct': round(_kazanc_pct, 1),
             'risk_pct': round(_risk_pct, 1),
             'taraf': _taraf,
             'hedef': round(_target, 4),
             'stop': round(_stop, 4)}
    if _r >= r_iyi:
        return {**_base, 'seviye': None,
                'metin': f"{_taraf} risk-ödül elverişli (≈{_r:.1f}R)"}
    if _r < r_kotu:
        return {**_base, 'seviye': 'kirmizi',
                'metin': (f"{_taraf} risk-ödül elverişsiz (≈{_r:.1f}R) — "
                          f"kazanç alanı dar (%{_kazanc_pct:.1f}), risk geniş "
                          f"(%{_risk_pct:.1f})")}
    return {**_base, 'seviye': 'amber',
            'metin': (f"{_taraf} risk-ödül sınırda (≈{_r:.1f}R) — kazanç alanı "
                      f"(%{_kazanc_pct:.1f}) riske (%{_risk_pct:.1f}) göre dar")}


def kapi_hukmu(likidite_tier=None, manip=None, risk_odul=None, adv_mn=None):
    """GÜVENLİK KAPISI birleşik hükmü (21 Tem 2026) — likidite + risk-ödül tek sonuçta.
    Codex: iki ayrı uyarı yerine tek 'KAPI: GEÇTİ/TEMKİNLİ/ZAYIF' hükmü.

    Girdi: likidite_tier ('düşük'/...) · manip ('orta'/'yüksek'/...) · risk_odul
    (risk_odul_kapisi çıktısı) · adv_mn (günlük ort TL ciro, mn).
    "Sorun yoksa gösterme" ilkesi: temizse None döner (GEÇTİ sessiz). Betimleyici —
    'işlem yapma' demez, kalite kapısının durumunu tarif eder. SOMUT SAYILAR sebepte
    kalır (R, %, TL ciro) — terminalin sayı odaklı çizgisi.

    Dönüş: {'durum','etiket','renk','sebep'} veya None."""
    _ro = risk_odul if isinstance(risk_odul, dict) else {}
    _r, _kp, _rp = _ro.get('r'), _ro.get('kazanc_pct'), _ro.get('risk_pct')

    def _ro_txt(sifat):
        if _r is not None and _kp is not None and _rp is not None:
            return f"risk-ödül ≈{_r:.1f}R {sifat} (kazanç %{_kp:.0f}, risk %{_rp:.0f})"
        return f"risk-ödül {sifat}"
    _liq_txt = (f"likidite ~{adv_mn}mn TL/gün — ince tahta"
                if adv_mn else "düşük likidite — ince tahta")

    zayif, temkinli = [], []
    if _ro.get('seviye') == 'kirmizi':
        zayif.append(_ro_txt("elverişsiz"))
    if likidite_tier == 'düşük':
        temkinli.append(_liq_txt)
    if manip in ('orta', 'yüksek'):
        temkinli.append(f"pompa riski ({manip})")
    if _ro.get('seviye') == 'amber':
        temkinli.append(_ro_txt("sınırda"))
    if zayif:
        return {'durum': 'zayif', 'etiket': 'ZAYIF', 'renk': '#ef4444',
                'sebep': " · ".join(zayif + temkinli)}
    if temkinli:
        return {'durum': 'temkinli', 'etiket': 'TEMKİNLİ', 'renk': '#f59e0b',
                'sebep': " · ".join(temkinli)}
    return None  # GEÇTİ — sorun yok, gösterme
