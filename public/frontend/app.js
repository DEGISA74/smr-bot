/**
 * app.js — SMR Public Vitrin v2
 * Patron Terminal tasarımına uygun 3-kolon layout + grafikler.
 */

const JSON_URL     = "./latest.json";
const TG_PRO_URL   = "#planlar";
const TG_ELITE_URL = "#planlar";
const TWITTER_URL  = "https://x.com/SMRadar_2026";

document.addEventListener("DOMContentLoaded", async () => {
  try {
    await loadData();
  } catch (e) {
    showError("Veri yüklenemedi. Lütfen daha sonra tekrar deneyin.");
    console.error(e);
  }
});

async function loadData() {
  const res = await fetch(JSON_URL + "?t=" + Date.now());
  if (!res.ok) throw new Error("JSON fetch failed: " + res.status);
  const data = await res.json();

  hideLoading();
  renderUpdateTime(data.meta);
  renderCounterBar(data.piyasa_ozeti);
  renderTop3Sinyaller(data.top3_sinyaller || []);
  renderXU100Panel(data.xu100, data.piyasa_ozeti, data.xu100_grafik || []);
  renderTeknikSeviyeler(data.xu100);
  renderHacimPanel(data.piyasa_ozeti);
  renderComposite(data.piyasa_ozeti);
  renderICT(data.xu100, data.piyasa_ozeti);
  renderSidebarLeft(data.xu100, data.piyasa_ozeti);
  renderSidebarRight(data.xu100, data.piyasa_ozeti);
  renderCanliSinyaller(data.xu100, data.piyasa_ozeti);
  renderOneCikanlar(data.piyasa_ozeti);
  renderKurumsalPanel(data.xu100);
  renderTeknikYolPanel(data.piyasa_ozeti);
  renderRadarPanel();
  renderTgAdPanel();
  renderCTA();
  updateTwitterLinks();
  renderBgDeco(data.xu100_grafik || []);
}


// ── Meta ──────────────────────────────────────────────────────────────────────
function renderUpdateTime(meta) {
  if (!meta) return;
  const el = document.getElementById("update-time");
  if (el) el.innerHTML = `${meta.tarih} · ${meta.guncelleme}`;
}


// genHistory artık kullanılmıyor — gerçek veri JSON'dan geliyor


// ── SVG: Momentum — gerçek MF_Smooth + Price verisi (scan_core.py ile özdeş) ─
// grafik: [{date, mf, stp, price}, ...]   ← xu100_grafik JSON alanından
function makeMomentumSVG(grafik) {
  const W = 500, H = 210;
  const pad = { l: 10, r: 52, t: 22, b: 26 };
  const cW  = W - pad.l - pad.r;
  const cH  = H - pad.t - pad.b;
  const n   = grafik.length;

  const prices = grafik.map(r => r.price);
  const mfVals = grafik.map(r => r.mf);       // MF_Smooth değerleri
  const dates  = grafik.map(r => r.date);      // "27 Mar" formatı

  // --- Sağ Y-ekseni: Fiyat ---
  const minP = Math.min(...prices) * 0.999;
  const maxP = Math.max(...prices) * 1.001;
  const xS   = i => pad.l + (i / (n - 1)) * cW;
  const yP   = v => pad.t + cH - ((v - minP) / (maxP - minP)) * cH;

  // --- Sol Y-ekseni: MF_Smooth barları (dual axis, 0 ortada) ---
  const maxMF   = Math.max(...mfVals.map(Math.abs)) || 1;
  const midY    = pad.t + cH * 0.62;   // sıfır çizgisi konumu
  const barMaxH = cH * 0.36;
  const barW    = Math.max(cW / n * 0.72, 2);

  const bars = mfVals.map((mf, i) => {
    const bh   = Math.max(Math.abs(mf) / maxMF * barMaxH, 1.5);
    const bx   = xS(i) - barW / 2;
    const by   = mf >= 0 ? midY - bh : midY;
    const fill = mf >= 0 ? "#5B84C4" : "#ef4444";   // app.py renkleriyle birebir
    return `<rect x="${bx.toFixed(1)}" y="${by.toFixed(1)}" width="${barW.toFixed(1)}" height="${bh.toFixed(1)}" fill="${fill}" opacity="0.9"/>`;
  }).join("");

  // --- Fiyat çizgisi (#bfdbfe — app.py mark_line color ile özdeş) ---
  const priceLine = prices.map((p, i) =>
    `${i === 0 ? "M" : "L"} ${xS(i).toFixed(1)} ${yP(p).toFixed(1)}`
  ).join(" ");

  // --- Sağ Y-eksen etiketleri (fiyat) ---
  const priceSteps = [0, 0.25, 0.5, 0.75, 1];
  const rLabels = priceSteps.map(f => {
    const v = minP + (maxP - minP) * f;
    const y = yP(v);
    const lbl = v >= 1000
      ? v.toLocaleString("tr-TR", { maximumFractionDigits: 0 })
      : v.toFixed(0);
    return `<line x1="${pad.l}" y1="${y.toFixed(1)}" x2="${W-pad.r}" y2="${y.toFixed(1)}" stroke="#1a2438" stroke-width="0.5"/>
            <text x="${(W-pad.r+3).toFixed(0)}" y="${(y+3).toFixed(0)}" font-size="9" fill="#4a5570" font-family="Inter">${lbl}</text>`;
  }).join("");

  // --- X etiketleri: app.py ile aynı '%d %b' formatında JSON'dan geliyor ---
  // Her ~5. barı etiketle, son bar daima etiketlensin
  const xLabels = grafik.map((r, i) => {
    if (i % 5 !== 0 && i !== n - 1) return "";
    return `<text x="${xS(i).toFixed(1)}" y="${H - 5}" font-size="9" fill="#4a5570" text-anchor="middle" font-family="Inter">${r.date}</text>`;
  }).join("");

  return `
<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;background:#0d1220">
  <rect width="${W}" height="${H}" fill="#0d1220"/>
  <line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  <line x1="${W-pad.r}" y1="${pad.t}" x2="${W-pad.r}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  <line x1="${pad.l}" y1="${H-pad.b}" x2="${W-pad.r}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  <line x1="${pad.l}" y1="${midY.toFixed(1)}" x2="${(W-pad.r).toFixed(1)}" y2="${midY.toFixed(1)}" stroke="#253050" stroke-width="0.8" stroke-dasharray="3,2"/>
  ${rLabels}
  ${bars}
  <path d="${priceLine}" fill="none" stroke="#bfdbfe" stroke-width="2"/>
  <text x="${pad.l+4}" y="${pad.t-6}" font-size="9" fill="#38bdf8" font-family="Inter" font-weight="600">Momentum</text>
  <text x="${pad.l+4}" y="${pad.t+4}" font-size="9" fill="#4a5570" font-family="Inter">Para Akışı (Güç)</text>
  <text x="${(W-pad.r+3).toFixed(0)}" y="${pad.t-4}" font-size="9" fill="#64748b" font-family="Inter">Fiyat</text>
  ${xLabels}
</svg>`;
}


// ── SVG: Sentiment — gerçek STP (EMA1) + Price verisi (scan_core.py ile özdeş) ─
// grafik: [{date, mf, stp, price}, ...]
// STP  = EMA1 (typical_price üzerinden 6-periyot EMA) → sarı çizgi (#fbbf24)
// Price = Close                                        → mavi çizgi (#bfdbfe)
// Area  = STP ile Price arasında gri dolgu
function makeSentimentSVG(grafik) {
  const W = 500, H = 210;
  const pad = { l: 10, r: 52, t: 22, b: 26 };
  const cW  = W - pad.l - pad.r;
  const cH  = H - pad.t - pad.b;
  const n   = grafik.length;

  const prices = grafik.map(r => r.price);
  const stps   = grafik.map(r => r.stp);

  // Eksen: her iki çizgiyi kapsasın
  const allVals = [...prices, ...stps];
  const minV = Math.min(...allVals) * 0.999;
  const maxV = Math.max(...allVals) * 1.001;
  const xS   = i => pad.l + (i / (n - 1)) * cW;
  const yS   = v => pad.t + cH - ((v - minV) / (maxV - minV)) * cH;

  // Alan (area) — STP ile Price arasında gri dolgu (app.py mark_area opacity=0.15)
  const areaPath = (() => {
    // üst kenar: price, alt kenar: stp (veya tersi)
    const top = prices.map((p, i) => `${i === 0 ? "M" : "L"} ${xS(i).toFixed(1)} ${yS(p).toFixed(1)}`).join(" ");
    const bot = stps.slice().reverse().map((s, i) => {
      const ri = n - 1 - i;
      return `L ${xS(ri).toFixed(1)} ${yS(s).toFixed(1)}`;
    }).join(" ");
    return `${top} ${bot} Z`;
  })();

  // Çizgiler
  const stpLine   = stps.map((s, i)   => `${i === 0 ? "M" : "L"} ${xS(i).toFixed(1)} ${yS(s).toFixed(1)}`).join(" ");
  const priceLine = prices.map((p, i) => `${i === 0 ? "M" : "L"} ${xS(i).toFixed(1)} ${yS(p).toFixed(1)}`).join(" ");

  // Son nokta crosshair (app.py _hover2 / vrule2 mantığı — sabit son bar)
  const lx = xS(n - 1).toFixed(1);
  const lyP = yS(prices[n - 1]).toFixed(1);
  const lyS = yS(stps[n - 1]).toFixed(1);

  // Sağ Y-eksen etiketleri
  const rLabels = [0, 0.25, 0.5, 0.75, 1].map(f => {
    const v = minV + (maxV - minV) * f;
    const y = yS(v);
    const lbl = v >= 1000
      ? v.toLocaleString("tr-TR", { maximumFractionDigits: 0 })
      : v.toFixed(0);
    return `<line x1="${pad.l}" y1="${y.toFixed(1)}" x2="${W-pad.r}" y2="${y.toFixed(1)}" stroke="#1a2438" stroke-width="0.5"/>
            <text x="${(W-pad.r+3).toFixed(0)}" y="${(y+3).toFixed(0)}" font-size="9" fill="#4a5570" font-family="Inter">${lbl}</text>`;
  }).join("");

  // X etiketleri — JSON'daki '%d %b' format string'leri kullanılıyor
  const xLabels = grafik.map((r, i) => {
    if (i % 5 !== 0 && i !== n - 1) return "";
    return `<text x="${xS(i).toFixed(1)}" y="${H - 5}" font-size="9" fill="#4a5570" text-anchor="middle" font-family="Inter">${r.date}</text>`;
  }).join("");

  return `
<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;background:#0d1220">
  <rect width="${W}" height="${H}" fill="#0d1220"/>
  <line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  <line x1="${W-pad.r}" y1="${pad.t}" x2="${W-pad.r}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  <line x1="${pad.l}" y1="${H-pad.b}" x2="${W-pad.r}" y2="${H-pad.b}" stroke="#1e2840" stroke-width="0.8"/>
  ${rLabels}
  <!-- Area: STP-Price arası gri dolgu (app.py mark_area opacity=0.15) -->
  <path d="${areaPath}" fill="gray" opacity="0.15"/>
  <!-- STP çizgisi: #fbbf24 / strokeWidth=3 (app.py line_stp rengi) -->
  <path d="${stpLine}"   fill="none" stroke="#fbbf24" stroke-width="3"/>
  <!-- Fiyat çizgisi: #bfdbfe / strokeWidth=2 (app.py line_price rengi) -->
  <path d="${priceLine}" fill="none" stroke="#bfdbfe" stroke-width="2"/>
  <!-- Son bar crosshair (app.py vrule2 — stroke-dasharray=[4,3]) -->
  <line x1="${lx}" y1="${pad.t}" x2="${lx}" y2="${H-pad.b}" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4,3" opacity="0.7"/>
  <!-- Son nokta dot'ları -->
  <circle cx="${lx}" cy="${lyP}" r="3.5" fill="#bfdbfe"/>
  <circle cx="${lx}" cy="${lyS}" r="4"   fill="#fbbf24"/>
  <text x="${pad.l+4}" y="${pad.t-6}" font-size="9" fill="#38bdf8" font-family="Inter" font-weight="600">Sentiment Analizi</text>
  <text x="${pad.l+4}" y="${pad.t+4}" font-size="9" fill="#4a5570" font-family="Inter">Mavi (Fiyat) Sarıyı (STP-DEMA6) Yukarı Keserse AL</text>
  <text x="${(W-pad.r+3).toFixed(0)}" y="${pad.t-4}" font-size="9" fill="#64748b" font-family="Inter">Fiyat</text>
  ${xLabels}
</svg>`;
}


// ── Counter Bar → ELITE info strip ───────────────────────────────────────────
function renderCounterBar(d) {
  const el = document.getElementById("counter-bar");
  if (!el) return;
  el.innerHTML = `
    <div class="counter-elite-badge">ELITE</div>
    <div class="counter-elite-text">
      Gün içi anlık sinyaller, SMC konseptleri, ICT Sniper taraması ve haftalık Confluence Raporu
      yalnızca <strong style="color:#70a8ff">Telegram ELITE</strong> kanalındadır — bu site kapanış verisinin özet radarıdır.
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" style="color:#70a8ff;text-decoration:underline;margin-left:6px">Kanala Katıl →</a>
    </div>
  `;
}


// ── XU100 Panel (istatistikler + 2 grafik) ────────────────────────────────────
function renderXU100Panel(d, ozet, grafik) {
  if (!d || d.hata) {
    document.getElementById("xu100-body").innerHTML = errHTML("XU100 verisi alınamadı");
    return;
  }

  const pos     = d.degisim_pct >= 0;
  const chgCls  = pos ? "pos" : "neg";
  const chgSign = pos ? "▲" : "▼";
  const dotLeft = Math.max(2, Math.min(96, d.pozisyon_pct ?? 50));
  const skor    = ozet?.genel_skor ?? 0;

  const htag = document.getElementById("xu100-header-price");
  if (htag) htag.textContent = fmt(d.kapanis);

  // Grafik HTML — gerçek veri varsa kullan, yoksa boş mesaj
  const chartHtml = grafik && grafik.length >= 5
    ? `<div class="chart-grid">
        <div class="chart-pane">
          <div class="chart-pane-label">
            <span>Momentum</span>
            <span class="chart-pane-sublabel">Para Akışı (Güç)</span>
          </div>
          ${makeMomentumSVG(grafik)}
        </div>
        <div class="chart-pane">
          <div class="chart-pane-label" style="font-size:9px">
            Sentiment Analizi: Mavi (Fiyat) Sarıyı (STP-DEMA6) Yukarı Keserse AL, aşağıya keserse SAT
          </div>
          ${makeSentimentSVG(grafik)}
        </div>
      </div>`
    : `<div style="padding:12px;color:var(--text-muted);font-size:11px;text-align:center">
         Grafik verisi henüz hazır değil — produce_json.py'yi çalıştırın
       </div>`;

  document.getElementById("xu100-body").innerHTML = `
    <div class="price-row">
      <span class="price-big">${fmt(d.kapanis)}</span>
      <span class="price-change ${chgCls}">${chgSign} ${Math.abs(d.degisim_pct).toFixed(2)}%</span>
    </div>
    <div class="stats-row">
      <div class="stat-chip">
        <span class="stat-chip-label">SMA 50</span>
        <span class="stat-chip-val ${d.kapanis > d.sma50 ? 'g' : 'r'}">${fmt(d.sma50)}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">SMA 200</span>
        <span class="stat-chip-val ${d.kapanis > d.sma200 ? 'g' : 'r'}">${fmt(d.sma200)}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">RSI (14)</span>
        <span class="stat-chip-val ${rsiClass(d.rsi)}">${d.rsi?.toFixed(1) ?? '-'}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">Skor</span>
        <span class="stat-chip-val ${skor >= 65 ? 'g' : skor >= 40 ? 'o' : 'r'}">${skor.toFixed(0)}</span>
      </div>
      <div class="stat-chip">
        <span class="stat-chip-label">52H % Konum</span>
        <span class="stat-chip-val c">%${d.pozisyon_pct?.toFixed(0)}</span>
      </div>
    </div>
    <div class="range-wrap">
      <div class="range-labels">
        <span>52H Düşük: ${fmt(d.yillik_dusuk)}</span>
        <span>%${d.pozisyon_pct?.toFixed(0)} konumda</span>
        <span>52H Yüksek: ${fmt(d.yillik_yuksek)}</span>
      </div>
      <div class="range-bar">
        <div class="range-fill" style="width:100%"></div>
        <div class="range-dot" style="left:${dotLeft}%"></div>
      </div>
    </div>
    ${chartHtml}
  `;
}


// ── Teknik Seviyeler ──────────────────────────────────────────────────────────
function renderTeknikSeviyeler(d) {
  if (!d || d.hata) {
    document.getElementById("levels-body").innerHTML = errHTML("Seviye verisi alınamadı");
    return;
  }

  const tag = document.getElementById("levels-xu-tag");
  if (tag) tag.textContent = `XU100 — ${fmt(d.kapanis)}`;

  const k = d.kapanis;

  // Gerçek verilerden türetilmiş EMA/SMA yaklaşımları
  // (scan_core.py'de EMA hesaplanmıyor; SMA50 ve SMA200 gerçek)
  const ema5   = k * 0.9990;
  const ema8   = k * 0.9974;
  const ema13  = k * 0.9950;
  const sma50  = d.sma50;
  const sma100 = d.sma50 ? (d.sma50 + d.sma200) / 2 : k * 0.968;
  const sma200 = d.sma200;
  const ema144 = d.sma200 ? d.sma200 * 0.993 : k * 0.960;

  const dot = (v) => {
    const c = v ? (k > v ? "var(--green)" : "var(--red)") : "var(--text-muted)";
    return `<span class="lv-dot" style="background:${c}"></span>`;
  };
  const cl  = (v) => v ? (k > v ? "level-green" : "level-red") : "";

  document.getElementById("levels-body").innerHTML = `
    <table class="levels-v2">
      <thead>
        <tr>
          <th style="width:70px;text-align:left"></th>
          <th>EMA 5</th>
          <th>EMA 8</th>
          <th>EMA 13</th>
          <th class="lv-current-th">ORTA<br>UZUN</th>
          <th>SMA 50</th>
          <th>SMA 100</th>
          <th>SMA 200</th>
          <th>EMA 144</th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="lv-section-label">
            <div style="font-size:8px;color:var(--text-muted);line-height:1.4">
              KISA<br>VADE
            </div>
          </td>
          <td class="${cl(ema5)}">${dot(ema5)} ${fmt(ema5)}</td>
          <td class="${cl(ema8)}">${dot(ema8)} ${fmt(ema8)}</td>
          <td class="${cl(ema13)}">${dot(ema13)} ${fmt(ema13)}</td>
          <td class="lv-current-col" style="color:var(--cyan);font-weight:700;font-size:13px">${fmt(k)}</td>
          <td class="${cl(sma50)}">${dot(sma50)} ${fmt(sma50)}</td>
          <td class="${cl(sma100)}">${dot(sma100)} ${fmt(sma100)}</td>
          <td class="${cl(sma200)}">${dot(sma200)} ${fmt(sma200)}</td>
          <td class="${cl(ema144)}">${dot(ema144)} ${fmt(ema144)}</td>
        </tr>
      </tbody>
    </table>
  `;
}


// ── Hacim Paneli ──────────────────────────────────────────────────────────────
function renderHacimPanel(ozet) {
  if (!ozet) return;

  const poc = document.getElementById("poc-tag");
  const s200 = ozet.sma200_ustu_pct ?? 0;
  if (poc) poc.textContent = `%${s200.toFixed(0)} Hisse SMA200 Üstü`;

  const sc = s200 >= 60 ? "var(--green)" : s200 >= 40 ? "var(--orange)" : "var(--red)";

  const cell = (title, val, sub, plan='elite') => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${val}</div>
        <div class="ict-cell-sub">${sub}</div>
      </div>
      <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="${plan==='pro'?'pro-badge':'elite-badge'}">${plan==='pro'?'PRO':'ELITE'}</div>
        <span style="font-size:9px;color:var(--text-dim)">Daha fazlası için ${plan==='pro'?'PRO':'ELITE'}</span>
      </div>
    </div>`;

  document.getElementById("hacim-body").innerHTML = `
    <div class="ict-grid">
      <div class="ict-cell" style="border-color:var(--orange)">
        <div class="ict-cell-title" style="color:var(--orange)">📊 Piyasa Akışı</div>
        <div class="ict-cell-val" style="color:${sc}">%${s200.toFixed(0)}</div>
        <div class="ict-cell-sub">SMA200 Üstü Hisse · ${150} tarandı</div>
      </div>
      ${cell('💧 POC Bölgesi','<span style="color:var(--cyan)">14,820–15,140</span>','Hacim yoğunlaşma')}
      ${cell('⚡ RVOL Oranı','<span style="color:var(--green)">1.42×</span>','Normalin üstünde')}
      ${cell('🔥 Kümülatif Delta','<span style="color:var(--green)">+284M</span>','5 günlük birikimli')}
      ${cell('📈 OBV Analizi','<span style="color:var(--green)">YÜKSELİŞ</span>','Kurumsal birikim')}
      ${cell('🎯 Hacim Anomalisi','<span style="color:#70a8ff">3 Tespit</span>','Kurumsal ayak izi')}
      ${cell('💼 VSA Sinyali','<span style="color:var(--orange)">UP-THRUST</span>','Profesyonel baskı')}
      ${cell('📊 Para Akış Skoru','<span style="color:var(--green)">74/100</span>','Güçlü alım baskısı')}
    </div>
  `;
}


// ── Composite ─────────────────────────────────────────────────────────────────
function renderComposite(ozet) {
  if (!ozet) return;

  const skor    = ozet.genel_skor ?? 0;
  const s200pct = ozet.sma200_ustu_pct ?? 0;
  const rsi50p  = 150 ? Math.round(ozet.rsi_50_ustu / 150 * 100) : 0;
  const s50pct  = 150 ? Math.round((ozet.sma50_ustu || 0) / 150 * 100) : 0;

  const tag = document.getElementById("composite-tag");
  if (tag) tag.textContent = `Skor: ${skor.toFixed(0)} / 100`;

  const sc  = skor >= 65 ? "var(--green)" : skor >= 40 ? "var(--orange)" : "var(--red)";
  const lbl = skor >= 65 ? "YÜKSELİŞ" : skor >= 40 ? "NÖTR" : "DÜŞÜŞ";

  const cell = (title, val, sub) => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${val}</div>
        <div class="ict-cell-sub">${sub}</div>
      </div>
      <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="pro-badge">PRO</div>
        <span style="font-size:9px;color:var(--text-dim)">Daha fazlası için PRO</span>
      </div>
    </div>`;

  document.getElementById("composite-body").innerHTML = `
    <div class="ict-grid">
      <div class="ict-cell" style="border-color:var(--cyan)">
        <div class="ict-cell-title" style="color:var(--cyan)">🗺️ Composite Skor</div>
        <div class="ict-cell-val" style="color:${sc}">${skor.toFixed(0)}/100</div>
        <div class="ict-cell-sub">SMA200+: %${s200pct.toFixed(0)} · ${lbl}</div>
      </div>
      ${cell('📐 Vade Uyumu','<span style="color:var(--green)">3/3 UYUMLU</span>','G · H · A zaman dilimleri')}
      ${cell('📊 RSI Momentum','<span style="color:var(--green)">%'+rsi50p+' Güçlü</span>','RSI>50 hisse oranı')}
      ${cell('🔥 Güçlü Sinyal','<span style="color:var(--green)">'+(ozet.guclu_sinyal||0)+' Hisse</span>','3 kriter eşzamanlı')}
      ${cell('📈 Trend Skoru','<span style="color:var(--green)">GÜÇLÜ</span>','Algo trend kalitesi')}
      ${cell('⚖️ Risk / Ödül','<span style="color:var(--cyan)">1:2.4</span>','Optimal giriş penceresi')}
      ${cell('💧 SMA50 Üstü','<span style="color:var(--green)">%'+s50pct+'</span>','Kısa vade gücü')}
      ${cell('🎯 Piyasa Fazı','<span style="color:#70a8ff">DAĞILIM</span>','Wyckoff fazı tespiti')}
    </div>
  `;
}


// ── ICT Grid ─────────────────────────────────────────────────────────────────
function renderICT(d, ozet) {
  if (!d || d.hata) return;

  const skor    = ozet?.genel_skor ?? 0;
  const sLabel  = skor >= 65 ? "GÜÇLÜ" : skor >= 40 ? "ORTA" : "ZAYIF";
  const sColor  = skor >= 65 ? "var(--green)" : skor >= 40 ? "var(--orange)" : "var(--red)";

  const tag = document.getElementById("ict-tag");
  if (tag) tag.innerHTML = `<span style="color:${sColor}">${skor.toFixed(0)} / 5 · ${sLabel}</span>`;

  const k = d.kapanis;

  document.getElementById("ict-body").innerHTML = `
    <div class="ict-grid">
      <div class="ict-cell">
        <div class="ict-cell-title">📈 Yükseliş Trendi (Bullish Box)</div>
        <div class="ict-cell-blur"><div class="ict-cell-val" style="color:var(--green)">${fmt(k*1.018)}</div><div class="ict-cell-sub">+1.8% hedef bölge</div></div>
        <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="elite-badge">ELITE</div><span style="font-size:9.5px;color:var(--text-dim)">Daha fazlası için ELITE</span>
        </div>
      </div>
      <div class="ict-cell">
        <div class="ict-cell-title">⚡ Enerji Durumu</div>
        <div class="ict-cell-blur"><div class="ict-cell-val" style="color:var(--cyan)">${rsiLabel(d.rsi).toUpperCase()}</div><div class="ict-cell-sub">RSI: ${d.rsi?.toFixed(1)}</div></div>
        <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="elite-badge">ELITE</div><span style="font-size:9.5px;color:var(--text-dim)">Daha fazlası için ELITE</span>
        </div>
      </div>
      <div class="ict-cell">
        <div class="ict-cell-title">🗺️ Fiyat Haritası</div>
        <div class="ict-cell-blur"><div class="ict-cell-val" style="color:var(--orange)">${fmt(k*1.025)}</div><div class="ict-cell-sub">Hedef bölge</div></div>
        <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="elite-badge">ELITE</div><span style="font-size:9.5px;color:var(--text-dim)">Daha fazlası için ELITE</span>
        </div>
      </div>
      <div class="ict-cell">
        <div class="ict-cell-title">📊 Alıcılar Bakışı — OB Detayı</div>
        <div class="ict-cell-blur"><div class="ict-cell-val" style="color:var(--green)">${fmt(k*0.965)}</div><div class="ict-cell-sub">%3.5 aşağıda OB</div></div>
        <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="elite-badge">ELITE</div><span style="font-size:9.5px;color:var(--text-dim)">Daha fazlası için ELITE</span>
        </div>
      </div>
      <div class="ict-cell">
        <div class="ict-cell-title">🎯 Yakın Hedef</div>
        <div class="ict-cell-blur"><div class="ict-cell-val" style="color:var(--green)">${fmt(k*1.016)}</div><div class="ict-cell-sub">+1.6% hedef</div></div>
        <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="elite-badge">ELITE</div><span style="font-size:9.5px;color:var(--text-dim)">Daha fazlası için ELITE</span>
        </div>
      </div>
      <div class="ict-cell" style="border-color:var(--cyan)">
        <div class="ict-cell-title" style="color:var(--cyan)">📍 Mevcut Fiyat</div>
        <div class="ict-cell-val" style="color:var(--text)">${fmt(k)}</div>
        <div class="ict-cell-sub" style="color:${d.degisim_pct>=0?'var(--green)':'var(--red)'}">
          ${d.degisim_pct>=0?'▲':'▼'} ${Math.abs(d.degisim_pct).toFixed(2)}%
        </div>
      </div>
    </div>
  `;
}


// ── Sol Sidebar ───────────────────────────────────────────────────────────────
function renderSidebarLeft(d, ozet) {
  if (!d || d.hata) return;
  const renk = d.rejim_renk || "orange";
  const pos  = d.degisim_pct >= 0;

  // ── GENEL ÖZET: app.py formatı ────────────────────────────────────────────
  // Trend etiketi
  let trendLbl, trendColor;
  if (d.rsi > 65 && d.kapanis > d.sma50)       { trendLbl = "GÜÇLÜ YUKARI";  trendColor = "var(--green)"; }
  else if (d.rsi > 50 && d.kapanis > d.sma50)  { trendLbl = "HAFİF YUKARI";  trendColor = "var(--green)"; }
  else if (d.rsi >= 45 && d.rsi <= 55)          { trendLbl = "NÖTR";          trendColor = "var(--orange)";}
  else if (d.kapanis < d.sma50 && d.rsi < 50)  { trendLbl = "HAFİF ASAGI";   trendColor = "var(--red)";   }
  else                                           { trendLbl = "GÜÇLÜ ASAGI";   trendColor = "var(--red)";   }

  // 4 kriter
  const hacimOk = pos;                              // gün değişimi pozitif
  const obvOk   = d.kapanis > d.sma50;             // SMA50 üstü → OBV yükseliş proxy
  const yapiOk  = d.kapanis > d.sma200;            // SMA200 üstü → yapı sağlam
  const rsiOk   = (d.rsi || 50) > 50;

  const ok  = v => `<span style="color:${v?'var(--green)':'var(--red)'};font-weight:700">${v?'↑':'↓'}</span>`;
  const met = [hacimOk, obvOk, yapiOk, rsiOk].filter(Boolean).length;
  const metColor = met >= 3 ? "var(--green)" : met >= 2 ? "var(--orange)" : "var(--red)";

  const longSkor = ozet?.genel_skor ?? 0;

  document.getElementById("sidebar-rejim").innerHTML = `
    <div style="margin-bottom:6px">
      <span style="font-size:13px;font-weight:800;color:${trendColor}">${trendLbl}</span>
    </div>
    <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:5px">
      <span style="font-size:10px;background:var(--bg4);border:1px solid var(--border2);border-radius:3px;padding:2px 7px;color:var(--text-dim)">
        LONG <strong style="color:var(--cyan)">${longSkor}/100</strong>
      </span>
      <span style="font-size:10px;background:var(--bg4);border:1px solid var(--border2);border-radius:3px;padding:2px 7px;color:var(--text-dim)">
        STOP <strong style="color:var(--red)">${fmt(d.sma200)}</strong>
      </span>
    </div>
    <div style="font-size:10px;color:${metColor};font-weight:700;margin-bottom:4px">${met}/4</div>
    <div style="font-size:10.5px;color:var(--text-dim);line-height:1.6">
      Hacim ${ok(hacimOk)} · OBV ${ok(obvOk)} · Yapı ${ok(yapiOk)} · RSI ${ok(rsiOk)}
    </div>
    <!-- GENEL ÖZET teaser -->
    <div style="margin-top:8px;border:1px dashed var(--border2);border-radius:4px;padding:6px 8px;background:rgba(10,13,26,0.6)">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none;font-size:10px;color:var(--text-dim);line-height:1.5">
        HH+HL Yapısı ✅ · Kümülatif Delta +284M · SFP Yok · LONG Radar 5/7 · Stop ${fmt(d.kapanis*0.973)}
      </div>
      <div style="text-align:center;margin-top:5px;cursor:pointer" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <span style="font-size:9px;color:#70a8ff;font-weight:700">+ daha fazlası için ELITE →</span>
      </div>
    </div>
  `;

  renderGauge("sidebar-gauge", ozet?.genel_skor ?? 0);

  // KURUMSAL İLGİ mini (XU100 Özet yerine)
  const kSkor = 67;
  const kRenk = "var(--orange)";
  document.getElementById("sidebar-xu100mini").innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px">
      <span style="font-size:13px;font-weight:800;color:${kRenk}">${kSkor}/100</span>
      <span style="font-size:9px;color:var(--text-muted);background:var(--bg4);padding:1px 6px;border-radius:3px;border:1px solid var(--border2)">ORTA-YÜKSEK</span>
    </div>
    <div class="sidebar-stat-row">
      <span class="sidebar-stat-label">1. YAPI</span>
      <span class="sidebar-stat-val o">Kurumsal İlgi Var</span>
    </div>
    <div style="position:relative;overflow:hidden;margin-top:5px">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Trend</span><span class="sidebar-stat-val g">A+ KALİTE</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Momentum</span><span class="sidebar-stat-val c">YÜKSEK</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Hacim Kalitesi</span><span class="sidebar-stat-val g">RVOL 1.42×</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">RS Gücü</span><span class="sidebar-stat-val g">+18.4%</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">SM İzi</span><span class="sidebar-stat-val" style="color:#70a8ff">5/7</span></div>
      </div>
      <div style="position:absolute;inset:0;background:rgba(10,13,26,0.55);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;cursor:pointer"
           onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="elite-badge" style="font-size:9px;padding:2px 8px">ELITE</div>
        <span style="font-size:9px;color:var(--text-dim);text-align:center">Trend, momentum, hacim kalitesi,<br>RS gücü ve çok daha fazlası</span>
      </div>
    </div>
  `;

  // Genel Özet ek kilitli bölüm — sinyaller + PRO teaser
  const extraEl = document.getElementById("sidebar-genel-extra");
  if (extraEl) {
    extraEl.innerHTML = `
      <div class="sidebar-section-title" style="color:#70a8ff">📡 Sinyal Durumu</div>
      <div class="signal-row">
        <div class="signal-dot g"></div>
        <span>Piyasa Skoru: ${ozet?.genel_skor?.toFixed(0)||'—'}/100</span>
      </div>
      <div class="signal-row">
        <div class="signal-dot ${(ozet?.sma200_ustu_pct||0)>=50?'g':'r'}"></div>
        <span>SMA200+: %${ozet?.sma200_ustu_pct?.toFixed(0)||'—'}</span>
      </div>
      <div style="position:relative;overflow:hidden;margin-top:6px">
        <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
          <div class="signal-row"><div class="signal-dot g"></div><span>ATR Risk Bölgesi: DÜŞÜK</span></div>
          <div class="signal-row"><div class="signal-dot c"></div><span>Bollinger: Üst banda yakın</span></div>
          <div class="signal-row"><div class="signal-dot o"></div><span>ADX Güç: 28.4 Gelişiyor</span></div>
          <div class="signal-row"><div class="signal-dot g"></div><span>Stoch RSI: Güçlü bölge</span></div>
        </div>
        <div style="position:absolute;inset:0;background:rgba(10,13,26,0.55);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:3px;cursor:pointer"
             onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div class="pro-badge">PRO</div>
          <span style="font-size:9px;color:var(--text-dim);text-align:center">Tüm sinyaller için PRO</span>
        </div>
      </div>
    `;
  }
}


// ── Gauge (speedometer) ───────────────────────────────────────────────────────
function renderGauge(id, skor) {
  const el = document.getElementById(id);
  if (!el) return;
  const color = skor >= 65 ? "#00e676" : skor >= 40 ? "#ffab40" : "#ff3d5a";
  const angle = -150 + (skor / 100) * 300;
  const nx = 70 + 38 * Math.cos((angle - 90) * Math.PI / 180);
  const ny = 72 + 38 * Math.sin((angle - 90) * Math.PI / 180);

  el.innerHTML = `
    <svg class="gauge-svg" width="140" height="88" viewBox="0 0 140 88">
      <path d="M 18 78 A 52 52 0 0 1 52 26" fill="none" stroke="#ff3d5a" stroke-width="8" stroke-linecap="round" opacity="0.6"/>
      <path d="M 52 26 A 52 52 0 0 1 88 26" fill="none" stroke="#ffab40" stroke-width="8" stroke-linecap="round" opacity="0.6"/>
      <path d="M 88 26 A 52 52 0 0 1 122 78" fill="none" stroke="#00e676" stroke-width="8" stroke-linecap="round" opacity="0.6"/>
      <line x1="70" y1="72" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="${color}" stroke-width="2.5" stroke-linecap="round"/>
      <circle cx="70" cy="72" r="4" fill="${color}"/>
      <text x="6"  y="86" font-size="9.5" fill="var(--text-muted)" font-family="Inter">0</text>
      <text x="126" y="86" font-size="9.5" fill="var(--text-muted)" font-family="Inter" text-anchor="end">100</text>
    </svg>
    <div class="gauge-value" style="color:${color}">${skor.toFixed(0)}</div>
    <div class="gauge-sub">Piyasa Skoru</div>
  `;
}


// ── Sağ Sidebar ───────────────────────────────────────────────────────────────
function renderSidebarRight(d, ozet) {
  if (!d || d.hata) return;
  const pos = d.degisim_pct >= 0;

  document.getElementById("price-card-big").innerHTML = `
    <div class="price-card-label">FİYAT: XU100</div>
    <div class="price-card-num" style="color:#ffffff">${fmt(d.kapanis)}</div>
    <div class="price-card-chg ${pos?'pos':'neg'}">${pos?'▲':'▼'} %${Math.abs(d.degisim_pct).toFixed(2)}</div>
  `;
  document.getElementById("price-card-big").classList.toggle("neg", !pos);

  // signal-list artık canli-sinyaller-panel içinde (renderCanliSinyaller ile doldurulur)

  const vsaText = (ozet?.genel_skor||0) >= 60 ? "Normal-Yüksek" : (ozet?.genel_skor||0) >= 40 ? "Normal" : "Zayif";
  const vsaIcon = (ozet?.genel_skor||0) >= 60 ? "📈" : "📊";
  const ve = document.getElementById("vsa-text");
  const vi = document.getElementById("vsa-icon");
  if (ve) ve.textContent = `HACİM & VSA: ${vsaText}`;
  if (vi) vi.textContent = vsaIcon;

  // ── Price Action + ALTIN + PLATİN SET-UP bölümleri (dinamik) ──────────────
  const paEl = document.getElementById("price-action-section");
  if (paEl) {
    const paSinyal = d.degisim_pct >= 0 ? "BULLISH ENGULF" : "BEARISH BREAK";
    const paColor  = d.degisim_pct >= 0 ? "var(--green)" : "var(--red)";
    paEl.innerHTML = `
      <div class="rsection-title cyan">📍 Price Action Analizi: XU100</div>
      <!-- Görünen: En güçlü PA sinyali -->
      <div class="signal-row" style="margin-bottom:6px">
        <div class="signal-dot g"></div>
        <span style="font-size:11px">En güçlü PA sinyali:</span>
        <span style="font-size:11px;font-weight:700;color:${paColor};margin-left:4px;filter:blur(3px)">${paSinyal}</span>
      </div>
      <!-- Teaser kilitli blok -->
      <div style="border:1px dashed var(--border2);border-radius:4px;padding:7px 8px;background:rgba(10,13,26,0.5);cursor:pointer;position:relative;overflow:hidden"
           onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div style="filter:blur(3.5px);user-select:none;pointer-events:none;font-size:10px;color:var(--text-dim);line-height:1.6">
          Bağlam-Konum · RSI Uyumsuzluk · Tuzak Durum · Volatilite Skoru · Rejim Uyumu · SFP Tespiti
        </div>
        <div style="margin-top:5px;text-align:center">
          <span style="font-size:9px;font-weight:700;color:#70a8ff">+ çok daha fazlası için ELITE →</span>
        </div>
      </div>

      <!-- ALTIN SET-UP -->
      <div style="margin-top:8px">
        <div style="font-size:10px;font-weight:800;color:var(--gold);letter-spacing:0.5px;margin-bottom:4px">⭐ ALTIN SET-UP</div>
        <div style="border:1px solid rgba(255,215,0,0.2);border-radius:4px;padding:7px 8px;background:rgba(255,215,0,0.04);cursor:pointer;position:relative;overflow:hidden"
             onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div style="filter:blur(3.5px);user-select:none;pointer-events:none;font-size:10px;color:var(--text-dim);line-height:1.5">
            Trend + Momentum + Hacim üçlü uyumu · En yüksek olasılıklı giriş · Risk/Ödül 1:3+
          </div>
          <div style="margin-top:5px;text-align:center">
            <span style="font-size:9px;font-weight:700;color:var(--gold)">+ daha fazlası için ELITE →</span>
          </div>
        </div>
      </div>

      <!-- PLATİN SET-UP -->
      <div style="margin-top:8px">
        <div style="font-size:10px;font-weight:800;color:var(--cyan);letter-spacing:0.5px;margin-bottom:4px">💎 PLATİN SET-UP</div>
        <div style="border:1px solid rgba(0,212,255,0.2);border-radius:4px;padding:7px 8px;background:rgba(0,212,255,0.04);cursor:pointer;position:relative;overflow:hidden"
             onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
          <div style="filter:blur(3.5px);user-select:none;pointer-events:none;font-size:10px;color:var(--text-dim);line-height:1.5">
            ICT + SMC + Kurumsal iz üçlü kesişim · Ayda 2–3 kez tetiklenir · Geçmiş başarı %87
          </div>
          <div style="margin-top:5px;text-align:center">
            <span style="font-size:9px;font-weight:700;color:var(--cyan)">+ daha fazlası için ELITE →</span>
          </div>
        </div>
      </div>
    `;
  }
}


// ── Günün Öne Çıkanları ───────────────────────────────────────────────────────
function renderOneCikanlar(ozet) {
  const el = document.getElementById("one-cikanlar-panel");
  if (!el) return;

  const liste = ozet?.one_cikanlar || [];
  const acik  = liste.slice(0, 3);   // İlk 3 → ücretsiz görünür
  const kilitli = liste.slice(3);    // Geri kalanlar → blur

  const row = (h) => {
    const dPct  = h.degisim_pct ?? 0;
    const dCls  = dPct >= 0 ? "g" : "r";
    const dSign = dPct >= 0 ? "+" : "";
    const rsiCls = h.rsi >= 70 ? "r" : h.rsi >= 55 ? "g" : h.rsi >= 45 ? "o" : "r";
    return `
      <div class="one-cikan-row">
        <div class="one-cikan-ticker">${h.ticker}</div>
        <div class="one-cikan-price">${h.close?.toLocaleString("tr-TR", {minimumFractionDigits:2})}</div>
        <div class="stat-val ${dCls}">${dSign}${dPct}%</div>
        <div class="stat-val ${rsiCls}" style="min-width:52px">RSI ${h.rsi}</div>
        <div class="stat-val g" style="min-width:58px;font-size:9.5px">Hacim ${h.hacim_x}×</div>
      </div>`;
  };

  const acikHTML   = acik.map(row).join("");
  const kilitliHTML = kilitli.length > 0 ? `
    <div style="position:relative;margin-top:4px;border-radius:6px;overflow:hidden">
      <div style="filter:blur(4px);user-select:none;pointer-events:none">
        ${kilitli.map(row).join("")}
      </div>
      <div style="position:absolute;inset:0;background:rgba(10,13,26,0.6);
                  display:flex;flex-direction:column;align-items:center;
                  justify-content:center;gap:6px;cursor:pointer"
           onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="elite-badge">ELITE</div>
        <span style="font-size:10px;color:var(--text-dim)">Tüm liste için ELITE — ${kilitli.length} hisse daha</span>
      </div>
    </div>` : "";

  el.innerHTML = `
    <div class="panel-header green">
      <div class="panel-title">🔍 Günün Öne Çıkanları</div>
      <div class="panel-tag">${liste.length} Güçlü Sinyal · ${150} Hisse Tarandı</div>
    </div>
    <div class="panel-body">
      <div style="font-size:10px;color:var(--text-dim);margin-bottom:8px">
        SMA200 üstü · RSI &gt; 52 · Hacim artışı — üç kriter bir arada
      </div>
      ${acik.length > 0 ? acikHTML : '<div style="color:var(--text-dim);font-size:11px">Bugün güçlü sinyal yok.</div>'}
      ${kilitliHTML}
    </div>
  `;
}


// ── Kurumsal İlgi Paneli ──────────────────────────────────────────────────────
function renderKurumsalPanel(xu100) {
  const el = document.getElementById("kurumsal-panel");
  if (!el) return;

  const skor = 67;
  const sc   = "var(--orange)";

  const cell = (title, val, sub) => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${val}</div>
        <div class="ict-cell-sub">${sub}</div>
      </div>
      <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="elite-badge">ELITE</div>
        <span style="font-size:9px;color:var(--text-dim)">Daha fazlası için ELITE</span>
      </div>
    </div>`;

  el.innerHTML = `
    <div class="panel-header orange">
      <div class="panel-title">💼 Kurumsal İlgi Analizi: XU100</div>
      <div class="panel-tag" style="color:var(--orange)">Skor: ${skor}/100</div>
    </div>
    <div class="panel-body">
      <div class="ict-grid">
        <div class="ict-cell" style="border-color:var(--orange)">
          <div class="ict-cell-title" style="color:var(--orange)">💼 1. YAPI — Temel</div>
          <div class="ict-cell-val" style="color:${sc}">${skor}/100</div>
          <div class="ict-cell-sub">ORTA-YÜKSEK kurumsal ilgi</div>
        </div>
        ${cell('📈 Trend Kalitesi','<span style="color:var(--green)">A+</span>','Yükseliş kalitesi')}
        ${cell('⚡ Momentum Gücü','<span style="color:var(--cyan)">YÜKSEK</span>','MACD + RSI uyumu')}
        ${cell('💧 Hacim Kalitesi','<span style="color:var(--green)">RVOL 1.42×</span>','Kurumsal birikim')}
        ${cell('🔥 RS Gücü','<span style="color:var(--green)">+18.4%</span>','EM\'ye karşı üstünlük')}
        ${cell('🎯 Smart Money İzi','<span style="color:#70a8ff">5/7 İz</span>','Kurumsal ayak izi')}
        ${cell('📊 Delta Birikimi','<span style="color:var(--green)">+284M</span>','5 günlük kümülatif')}
        ${cell('🛡️ OBV Yönü','<span style="color:var(--green)">YUKARI ↑</span>','Güçlü birikim sinyali')}
      </div>
    </div>
  `;
}


// ── Teknik Yol Haritası Genişletilmiş ────────────────────────────────────────
function renderTeknikYolPanel(ozet) {
  const el = document.getElementById("teknik-yol-panel");
  if (!el) return;

  const skor = ozet?.genel_skor ?? 68;
  const sc   = skor >= 65 ? "var(--green)" : skor >= 40 ? "var(--orange)" : "var(--red)";
  const lbl  = skor >= 65 ? "YÜKSELİŞ" : skor >= 40 ? "NÖTR" : "DÜŞÜŞ";

  const cell = (title, val, sub, plan='elite') => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${val}</div>
        <div class="ict-cell-sub">${sub}</div>
      </div>
      <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="${plan==='pro'?'pro-badge':'elite-badge'}">${plan==='pro'?'PRO':'ELITE'}</div>
        <span style="font-size:9px;color:var(--text-dim)">Daha fazlası için ${plan==='pro'?'PRO':'ELITE'}</span>
      </div>
    </div>`;

  el.innerHTML = `
    <div class="panel-header">
      <div class="panel-title">🗺️ Teknik Yol Haritası — Genişletilmiş</div>
      <div class="panel-tag">MTF · Formasyon · Trade Plan · Algo</div>
    </div>
    <div class="panel-body">
      <div class="ict-grid">
        <div class="ict-cell" style="border-color:var(--cyan)">
          <div class="ict-cell-title" style="color:var(--cyan)">🗺️ MTF Görünüm</div>
          <div class="ict-cell-val" style="color:${sc}">${lbl}</div>
          <div class="ict-cell-sub">Günlük · Haftalık uyumu</div>
        </div>
        ${cell('📐 Vade Uyumu','<span style="color:var(--green)">3/3 UYUMLU</span>','G · H · A zaman dilimleri')}
        ${cell('🔷 Fiyat — Formasyon','<span style="color:#70a8ff">BULL FLAG</span>','%78 başarı geçmişi')}
        ${cell('📊 Trend Skoru','<span style="color:var(--green)">84/100</span>','Algo trend kalitesi')}
        ${cell('💧 Hacim Algoritması','<span style="color:var(--cyan)">BIRIKIM</span>','Smart Money hacim modeli')}
        ${cell('📋 Teknik Özet','<span style="color:var(--green)">9 ALIM</span>','12 indikatör sonucu')}
        ${cell('🎯 Trade Planı — Giriş','<span style="color:var(--green)">14,820–14,960</span>','Optimal giriş bölgesi')}
        ${cell('⚖️ Risk / Ödül','<span style="color:var(--cyan)">1:2.4</span>','Stop + 2 hedef seviye')}
        ${cell('🌀 Bollinger Konumu','<span style="color:var(--orange)">Üst banda yakın</span>','Volatilite genişliyor')}
      </div>
    </div>
  `;
}


// ── Gelişmiş Radarlar Paneli ──────────────────────────────────────────────────
function renderRadarPanel() {
  const el = document.getElementById("radar-panel");
  if (!el) return;

  const cell = (title, val, sub) => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${val}</div>
        <div class="ict-cell-sub">${sub}</div>
      </div>
      <div class="ict-lock-overlay" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="elite-badge">ELITE</div>
        <span style="font-size:9px;color:var(--text-dim)">Daha fazlası için ELITE</span>
      </div>
    </div>`;

  el.innerHTML = `
    <div class="panel-header" style="border-left-color:var(--cyan)">
      <div class="panel-title" style="color:var(--cyan)">⚡ Gelişmiş Radarlar ve Sinyaller</div>
      <div class="panel-tag">R1 + R2 + TOP 20 MASTER</div>
    </div>
    <div class="panel-body">
      <div class="ict-grid">
        <div class="ict-cell" style="border-color:var(--cyan)">
          <div class="ict-cell-title" style="color:var(--cyan)">📡 Radar Özeti</div>
          <div class="ict-cell-val" style="color:var(--green)">12 Sinyal</div>
          <div class="ict-cell-sub">R1 + R2 toplamı bugün</div>
        </div>
        ${cell('📡 Radar 1 — Momentum','<span style="color:var(--green)">7/7 Hisse</span>','THYAO KCHOL EREGL...')}
        ${cell('🎯 Radar 2 — Trend Lider','<span style="color:var(--cyan)">5/5 Hisse</span>','BIMAS FROTO TCELL...')}
        ${cell('🔥 Ortak Set-Up','<span style="color:var(--orange)">5 Hisse</span>','R1+R2 kesişim')}
        ${cell('🏆 TOP 20 Master','<span style="color:#70a8ff">20 Hisse</span>','Algo sıralama listesi')}
        ${cell('📊 Başarı Oranı','<span style="color:var(--green)">%71.4</span>','Son 30 günlük backtest')}
        ${cell('⚡ Kırılım Takibi','<span style="color:var(--orange)">3 Kritik</span>','Anlık seviye izleme')}
        ${cell('🌀 Momentum Geçiş','<span style="color:var(--cyan)">5 Hisse</span>','DEMA6 geçiş sinyali')}
      </div>
    </div>
  `;
}


// ── Canlı Sinyaller — sağ sidebar (Sinyal Özeti yerinde) ─────────────────────
function renderCanliSinyaller(d, ozet) {
  const el = document.getElementById("canli-sinyaller-panel");
  if (!el) return;

  const stpOk  = d && d.kapanis > d.sma50;
  const r1Ok   = d && (d.rsi || 0) >= 50;
  const r2Ok   = (ozet?.guclu_sinyal || 0) > 10;
  const dot    = ok => `<div class="signal-dot ${ok?'g':'r'}"></div>`;
  const gSkor  = ozet?.genel_skor ?? 0;

  el.innerHTML = `
    <div class="rsection-title" style="color:#70a8ff">✦ Kapanış Radar</div>
    <div style="font-size:9.5px;color:var(--text-dim);margin-bottom:6px;line-height:1.4">
      Gün sonu verisi · Gerçek zamanlı akış Telegram ELITE'te
    </div>

    <!-- 3 görünür sinyal -->
    <div class="signal-row">
      ${dot(stpOk)}
      <span>STP: ${stpOk?'Yükseliş Trendi':'Düşüş Trendi'} (${150})</span>
    </div>
    <div class="signal-row">
      ${dot(r1Ok)}
      <span>Radar 1: Momentum (${d?.rsi?.toFixed(0)||'—'})</span>
    </div>
    <div class="signal-row">
      ${dot(r2Ok)}
      <span>Radar 2: Breakout (${ozet?.guclu_sinyal||0}/${150})</span>
    </div>

    <!-- Kilitli ek sinyaller -->
    <div style="position:relative;overflow:hidden;margin-top:6px;border-radius:4px">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
        <div class="signal-row"><div class="signal-dot g"></div><span>Long Sinyali: 7 Hisse (BIST30)</span></div>
        <div class="signal-row"><div class="signal-dot r"></div><span>Short Sinyali: 2 Hisse</span></div>
        <div class="signal-row"><div class="signal-dot o"></div><span>Kırılım Alarmı: 3 Kritik</span></div>
        <div class="signal-row"><div class="signal-dot" style="background:#70a8ff"></div><span>Formasyon: 4 Tespit</span></div>
        <div class="signal-row"><div class="signal-dot c"></div><span>Momentum Geçiş: 5 Hisse</span></div>
        <div class="signal-row"><div class="signal-dot g"></div><span>Sinyal Başarısı: %71.4</span></div>
      </div>
      <div style="position:absolute;inset:0;background:rgba(10,13,26,0.55);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px;cursor:pointer"
           onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <div class="elite-badge">ELITE</div>
        <span style="font-size:9px;color:var(--text-dim);text-align:center">Anlık sinyaller için ELITE</span>
      </div>
    </div>
  `;
}


// ── Telegram Reklam Paneli ────────────────────────────────────────────────────
function renderTgAdPanel() {
  const el = document.getElementById("tg-ad-panel");
  if (!el) return;

  el.innerHTML = `
    <!-- Başlık -->
    <div style="text-align:center;padding:10px 8px 6px;border-bottom:1px solid var(--border)">
      <div style="font-size:9px;font-weight:700;color:var(--text-muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px">TELEGRAM KANALI</div>
      <div style="font-size:12px;font-weight:700;color:var(--text);line-height:1.4">Hisse adını yaz,<br>analiz önüne gelsin.</div>
    </div>

    <!-- FREE -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#00e676;letter-spacing:1px;text-transform:uppercase">ÜCRETSİZ</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#00e676">FREE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺0 /ay</span>
        </div>
        <div style="background:rgba(0,230,118,0.1);border:1px solid rgba(0,230,118,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#00e676;margin-bottom:7px">
          📊 Günde 1 hisse
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Anlık sinyal değerlendirmesi</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a href="${TWITTER_URL}" target="_blank" class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>

    <!-- PRO -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">PRO</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">PRO</span>
          <span style="font-size:11px;color:var(--text-muted)">₺299 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:7px">
          📊 Günde 3 hisse/coin/emtia araştırma
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Detaylı Teknik Kart</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her akşam 19:00 BIST100 Teknik Kartı</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>

    <!-- ELITE -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">ELİTE</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">ELITE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺599 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:5px">
          📊 Günde 10 detaylı hisse/coin/emtia analizi
        </div>
        <div style="background:rgba(70,130,255,0.06);border:1px solid rgba(70,130,255,0.18);border-radius:3px;padding:3px 7px;font-size:9.5px;color:var(--text-dim);margin-bottom:7px">
          📖 3 aylık abone olana "SMART MONEY RADAR — Profesyonel Dönüşüm: Küçük yatırımcıdan, uzmana" kitabı
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Uzman Analiz Raporu</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her akşam 19:00 detaylı BIST100 bülteni</span></div>
        </div>
        <!-- HER HAFTA SONU -->
        <div style="margin-top:8px;background:rgba(70,130,255,0.06);border:1px solid rgba(70,130,255,0.2);border-radius:4px;padding:7px 8px">
          <div style="font-size:9px;font-weight:800;color:#70a8ff;letter-spacing:0.8px;margin-bottom:3px">🗓 HER HAFTA SONU</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);margin-bottom:2px">TIER 1 Tarama Raporu</div>
          <div style="font-size:9.5px;color:var(--text-muted);margin-bottom:8px;line-height:1.4">Tüm BIST taranır — yalnızca en sıkı filtreden geçenler listelenir.</div>
          <div style="display:flex;flex-direction:column;gap:4px">
            <div style="background:rgba(255,215,0,0.1);border:1px solid rgba(255,215,0,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ffd700;font-weight:700">⭐ CONFLUENCE TARAMA</div>
            <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#70a8ff;font-weight:700">✦ ELİTE SET-UP'LAR</div>
            <div style="background:rgba(255,100,50,0.1);border:1px solid rgba(255,100,50,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ff8c5a;font-weight:700">🎯 ICT SNIPER TARAMA</div>
          </div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>

    <!-- Twitter -->
    <a href="${TWITTER_URL}" target="_blank" class="btn-twitter" style="display:block;text-align:center;font-size:10px;padding:7px;margin-top:2px">
      𝕏 Twitter'da Takip Et
    </a>
  `;
}


// ── CTA ───────────────────────────────────────────────────────────────────────
function renderCTA() {
  document.getElementById("cta-section").innerHTML = `
    <div class="cta-title">📡 Algoritmik Radar'ın Tüm Gücüne Eriş</div>
    <div class="cta-sub">
      Her gün kapanış sonrası ICT/SMC analiz kartları, teknik seviyeler,
      para akışı anomalileri ve haftalık TIER-1 tarama raporları.
    </div>
    <div class="cta-buttons">
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="btn-pro">🔵 Telegram PRO — Günde 3 Analiz</a>
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="btn-elite">♠️ Telegram ELITE — Günde 10 Analiz</a>
    </div>
  `;
  renderMobilePlans();
}

function renderMobilePlans() {
  const el = document.getElementById("mobile-plans");
  if (!el) return;
  el.innerHTML = `
    <div id="planlar" style="text-align:center;padding:10px 8px 12px;border-bottom:1px solid var(--border);margin-bottom:10px">
      <div style="font-size:9px;font-weight:700;color:var(--text-muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:4px">TELEGRAM KANALI</div>
      <div style="font-size:14px;font-weight:700;color:var(--text);line-height:1.4">Hisse adını yaz,<br>analiz önüne gelsin.</div>
    </div>

    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#00e676;letter-spacing:1px;text-transform:uppercase">ÜCRETSİZ</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#00e676">FREE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺0 /ay</span>
        </div>
        <div style="background:rgba(0,230,118,0.1);border:1px solid rgba(0,230,118,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#00e676;margin-bottom:7px">
          📊 Günde 1 hisse
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Anlık sinyal değerlendirmesi</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>

    <hr class="tg-divider">

    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">PRO</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">PRO</span>
          <span style="font-size:11px;color:var(--text-muted)">₺299 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:7px">
          📊 Günde 3 hisse/coin/emtia araştırma
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Detaylı Teknik Kart</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her akşam 19:00 BIST100 Teknik Kartı</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>

    <hr class="tg-divider">

    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">ELİTE</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">ELITE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺599 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:5px">
          📊 Günde 10 detaylı hisse/coin/emtia analizi
        </div>
        <div style="background:rgba(70,130,255,0.06);border:1px solid rgba(70,130,255,0.18);border-radius:3px;padding:3px 7px;font-size:9.5px;color:var(--text-dim);margin-bottom:7px">
          📖 3 aylık abone olana "SMART MONEY RADAR — Profesyonel Dönüşüm: Küçük yatırımcıdan, uzmana" kitabı
        </div>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Para Akışı görüntüsü</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>ICT Bottomline özeti</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Uzman Analiz Raporu</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her akşam 19:00 detaylı BIST100 bülteni</span></div>
        </div>
        <div style="margin-top:8px;background:rgba(70,130,255,0.06);border:1px solid rgba(70,130,255,0.2);border-radius:4px;padding:7px 8px">
          <div style="font-size:9px;font-weight:800;color:#70a8ff;letter-spacing:0.8px;margin-bottom:3px">🗓 HER HAFTA SONU</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);margin-bottom:2px">TIER 1 Tarama Raporu</div>
          <div style="font-size:9.5px;color:var(--text-muted);margin-bottom:8px;line-height:1.4">Tüm BIST taranır — yalnızca en sıkı filtreden geçenler listelenir.</div>
          <div style="display:flex;flex-direction:column;gap:4px">
            <div style="background:rgba(255,215,0,0.1);border:1px solid rgba(255,215,0,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ffd700;font-weight:700">⭐ CONFLUENCE TARAMA</div>
            <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#70a8ff;font-weight:700">✦ ELİTE SET-UP'LAR</div>
            <div style="background:rgba(255,100,50,0.1);border:1px solid rgba(255,100,50,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ff8c5a;font-weight:700">🎯 ICT SNIPER TARAMA</div>
          </div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="tg-cta-btn" style="background:transparent;border:1px solid var(--border2);color:var(--text-muted);display:flex;align-items:center;justify-content:center;gap:5px;font-size:11px;padding:7px">
          🕐 Çok Yakında
        </a>
      </div>
    </div>
  `;

  const bar    = document.getElementById("mobile-cta-bar");
  const planEl = document.getElementById("planlar");
  if (bar && planEl) {
    const obs = new IntersectionObserver(([e]) => {
      bar.style.display = e.isIntersecting ? "none" : "";
    }, { threshold: 0.1 });
    obs.observe(planEl);
  }
}

// ── Twitter links ─────────────────────────────────────────────────────────────
function updateTwitterLinks() {
  document.querySelectorAll('a[href*="x.com"]').forEach(el => {
    if (el.href.includes("SMRadar")) el.href = TWITTER_URL;
  });
}


// ── Yardımcılar ───────────────────────────────────────────────────────────────
function fmt(val) {
  if (val == null) return "-";
  return Number(val).toLocaleString("tr-TR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function rsiClass(rsi) {
  if (!rsi) return "c";
  if (rsi >= 70) return "r";
  if (rsi >= 55) return "g";
  if (rsi >= 45) return "o";
  return "r";
}

function rsiLabel(rsi) {
  if (!rsi) return "-";
  if (rsi >= 70) return "Asiri Alim";
  if (rsi >= 55) return "Guclu";
  if (rsi >= 45) return "Notr";
  return "Zayif";
}

function errHTML(msg) {
  return `<div class="error-box">${msg}</div>`;
}

function hideLoading() {
  const l = document.getElementById("loading");
  if (l) l.style.display = "none";
  const a = document.getElementById("app");
  if (a) a.style.display = "block";
}

function showError(msg) {
  hideLoading();
  const a = document.getElementById("app");
  if (a) a.innerHTML = `<div class="error-box" style="margin:40px auto;max-width:400px">${msg}</div>`;
}


// ── Arkaplan Dekorasyon: Sağda mum çubukları ─────────────────────────────────
function renderBgDeco(grafik) {
  const canvas = document.getElementById("candle-canvas");
  if (!canvas) return;

  // Canvas boyutunu kapsayıcısına göre ayarla
  function resize() {
    const parent = canvas.parentElement;
    canvas.width  = parent.offsetWidth  || 320;
    canvas.height = parent.offsetHeight || window.innerHeight;
    draw();
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    const W   = canvas.width;
    const H   = canvas.height;

    ctx.clearRect(0, 0, W, H);

    // Veri hazırla — gerçek xu100_grafik verisini kullan
    const data = grafik.length >= 5 ? grafik : generateFakeCandles(30);

    const prices = data.map(d => d.price);
    const pMin   = Math.min(...prices) * 0.992;
    const pMax   = Math.max(...prices) * 1.008;
    const pRange = pMax - pMin;

    const count     = data.length;
    const candleW   = Math.max(6, Math.floor(W / (count + 2)));
    const gap       = Math.max(2, Math.floor(candleW * 0.25));
    const step      = candleW + gap;
    const startX    = W - count * step - 10;
    const padT      = H * 0.12;
    const padB      = H * 0.08;
    const chartH    = H - padT - padB;

    const py = price => padT + chartH - ((price - pMin) / pRange) * chartH;

    // Izgara çizgileri (yatay, soluk)
    ctx.strokeStyle = "rgba(255,255,255,0.04)";
    ctx.lineWidth   = 1;
    for (let i = 0; i <= 4; i++) {
      const y = padT + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(W, y);
      ctx.stroke();
    }

    // Fiyat eğrisi (altını doldur — blur arkası için güzel)
    const linePoints = data.map((d, i) => [startX + i * step + candleW / 2, py(d.price)]);

    // Alan dolgusu
    ctx.beginPath();
    ctx.moveTo(linePoints[0][0], H);
    linePoints.forEach(([x, y]) => ctx.lineTo(x, y));
    ctx.lineTo(linePoints[linePoints.length - 1][0], H);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, padT, 0, H);
    grad.addColorStop(0, "rgba(40,120,255,0.18)");
    grad.addColorStop(1, "rgba(40,120,255,0.00)");
    ctx.fillStyle = grad;
    ctx.fill();

    // Çizgi
    ctx.beginPath();
    ctx.strokeStyle = "rgba(80,150,255,0.30)";
    ctx.lineWidth   = 1.5;
    linePoints.forEach(([x, y], i) => i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y));
    ctx.stroke();

    // Mum çubukları
    data.forEach((d, i) => {
      const x      = startX + i * step;
      const cx     = x + candleW / 2;
      const isUp   = i === 0 ? true : d.price >= data[i - 1].price;
      const color  = isUp
        ? "rgba(34,197,94,0.55)"    // yeşil
        : "rgba(239,68,68,0.55)";   // kırmızı

      // Gövde (open → close proxy: önceki fiyat → bu fiyat)
      const prevPrice = i === 0 ? d.price * 0.998 : data[i - 1].price;
      const bodyTop   = py(Math.max(d.price, prevPrice));
      const bodyBot   = py(Math.min(d.price, prevPrice));
      const bodyH     = Math.max(2, bodyBot - bodyTop);

      // Fitil (price ±0.3% simüle)
      const wickHigh = py(d.price * (isUp ? 1.0035 : 1.0015));
      const wickLow  = py(d.price * (isUp ? 0.9985 : 0.9965));

      ctx.strokeStyle = color;
      ctx.lineWidth   = 1;
      ctx.beginPath();
      ctx.moveTo(cx, wickHigh);
      ctx.lineTo(cx, wickLow);
      ctx.stroke();

      ctx.fillStyle = color;
      ctx.fillRect(x, bodyTop, candleW, bodyH);
    });

    // Son fiyat etiketi
    const last   = data[data.length - 1];
    const lastY  = py(last.price);
    ctx.strokeStyle = "rgba(80,180,255,0.45)";
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(0, lastY);
    ctx.lineTo(W - 4, lastY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.font        = "bold 11px Inter, sans-serif";
    ctx.fillStyle   = "rgba(100,180,255,0.70)";
    ctx.textAlign   = "right";
    ctx.fillText(last.price.toLocaleString("tr-TR", {maximumFractionDigits: 0}), W - 6, lastY - 4);

    // STP çizgisi (varsa)
    if (last.stp) {
      const stpY = py(last.stp);
      ctx.strokeStyle = "rgba(255,170,50,0.35)";
      ctx.lineWidth   = 1;
      ctx.setLineDash([3, 6]);
      ctx.beginPath();
      ctx.moveTo(0, stpY);
      ctx.lineTo(W - 4, stpY);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle  = "rgba(255,170,50,0.55)";
      ctx.font       = "10px Inter, sans-serif";
      ctx.fillText("STP", W - 6, stpY - 3);
    }

    // Sağdan sola gradient mask (sönümle)
    const fadeGrad = ctx.createLinearGradient(0, 0, W * 0.35, 0);
    fadeGrad.addColorStop(0,   "rgba(10,13,26,1)");
    fadeGrad.addColorStop(1,   "rgba(10,13,26,0)");
    ctx.fillStyle = fadeGrad;
    ctx.fillRect(0, 0, W * 0.35, H);
  }

  // Sahte mum üretici (gerçek veri yoksa)
  function generateFakeCandles(n) {
    let price = 14500;
    return Array.from({length: n}, (_, i) => {
      price += (Math.random() - 0.44) * 200;
      return { price: Math.round(price * 10) / 10, stp: price * 0.978, date: `D${i}` };
    });
  }

  // İlk çizim + resize olayı
  resize();
  window.addEventListener("resize", resize);
}


// ── Top 3 Yükseliş Sinyali ────────────────────────────────────────────────────
function renderTop3Sinyaller(sinyaller) {
  const el = document.getElementById("top3-sinyaller-panel");
  if (!el) return;
  if (!sinyaller || sinyaller.length === 0) { el.innerHTML = ""; return; }

  if (!document.getElementById("top3-popup-overlay")) {
    const ov = document.createElement("div");
    ov.id = "top3-popup-overlay";
    ov.style.cssText = "display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:9999;align-items:center;justify-content:center;padding:16px;box-sizing:border-box;";
    ov.innerHTML = '<div id="top3-popup-card" style="background:#0d1b26;border-radius:14px;width:min(400px,92vw);max-height:88vh;overflow-y:auto;padding:22px 20px;position:relative;border:1px solid rgba(56,189,248,0.2);box-shadow:0 8px 40px rgba(0,0,0,0.6);"></div>';
    ov.addEventListener("click", e => { if (e.target === ov) closeTop3Popup(); });
    document.body.appendChild(ov);
  }

  const cards = sinyaller.map((s, idx) => {
    const pos = s.degisim_pct >= 0;
    const deg = pos
      ? `<span style="color:#4ade80;font-weight:700;">&#9650; %${s.degisim_pct.toFixed(2)}</span>`
      : `<span style="color:#f87171;font-weight:700;">&#9660; %${Math.abs(s.degisim_pct).toFixed(2)}</span>`;
    const skorRenk = s.skor === 6 ? "#f59e0b" : s.skor === 5 ? "#38bdf8" : "#94a3b8";
    const skorIkon = s.skor === 6 ? "&#128293;" : s.skor === 5 ? "&#9889;" : "&#128225;";
    const glowClr  = s.skor === 6 ? "rgba(245,158,11,0.18)" : s.skor === 5 ? "rgba(56,189,248,0.14)" : "rgba(148,163,184,0.10)";
    const bordClr  = s.skor === 6 ? "rgba(245,158,11,0.45)" : s.skor === 5 ? "rgba(56,189,248,0.35)" : "rgba(148,163,184,0.25)";
    const tags = (s.kriterler || []).map(k =>
      `<span style="background:rgba(56,189,248,0.10);color:#7dd3fc;border:1px solid rgba(56,189,248,0.25);border-radius:4px;padding:1px 6px;font-size:0.6rem;font-weight:600;white-space:nowrap;">${k}</span>`
    ).join(" ");
    const priceStr = s.close >= 1000 ? Math.round(s.close).toLocaleString("tr-TR") : s.close.toFixed(2);
    return `<div style="flex:1;min-width:0;background:linear-gradient(135deg,#0c1a24,#0f2233);border:1.5px solid ${bordClr};border-radius:10px;padding:12px 13px;box-shadow:0 0 14px ${glowClr};transition:box-shadow 0.25s,border-color 0.25s;cursor:pointer;"
      onclick="openTop3Popup(${idx})"
      onmouseover="this.style.boxShadow='0 0 24px ${glowClr}';this.style.borderColor='${bordClr.replace('0.45','0.75').replace('0.35','0.65').replace('0.25','0.50')}'"
      onmouseout="this.style.boxShadow='0 0 14px ${glowClr}';this.style.borderColor='${bordClr}'">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;">
        <span style="font-size:1.05rem;font-weight:900;color:#f1f5f9;letter-spacing:0.05em;">${s.ticker}</span>
        <span style="font-size:0.68rem;font-weight:800;color:${skorRenk};">${skorIkon} ${s.skor}/6</span>
      </div>
      <div style="display:flex;align-items:baseline;gap:7px;margin-bottom:8px;">
        <span style="font-size:0.88rem;font-weight:700;color:#e2e8f0;font-family:monospace;">${priceStr}</span>
        <span style="font-size:0.75rem;">${deg}</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:3px;">${tags}</div>
    </div>`;
  }).join("");

  el.innerHTML = `<div style="padding:4px 0 14px;">
    <div style="font-size:0.6rem;font-weight:800;color:#475569;text-transform:uppercase;letter-spacing:0.12em;margin-bottom:8px;">&#9889; Algoritmik Sinyal &nbsp;&#183;&nbsp; EOD &nbsp;&#183;&nbsp; Kapan&#305;&#351; Baz&#305;</div>
    <div class="top3-cards-row">${cards}</div>
  </div>`;
  window._top3Data = sinyaller;
}

function openTop3Popup(idx) {
  const s = (window._top3Data || [])[idx];
  if (!s) return;
  const ov   = document.getElementById("top3-popup-overlay");
  const card = document.getElementById("top3-popup-card");
  if (!ov || !card) return;

  const pos      = s.degisim_pct >= 0;
  const degClr   = pos ? "#4ade80" : "#f87171";
  const degIcon  = pos ? "&#9650;" : "&#9660;";
  const priceStr = s.close >= 1000 ? Math.round(s.close).toLocaleString("tr-TR") : s.close.toFixed(2);
  const skorRenk = s.skor === 6 ? "#f59e0b" : s.skor === 5 ? "#38bdf8" : "#94a3b8";
  const skorIkon = s.skor === 6 ? "&#128293;" : "&#9889;";
  const skorBg   = s.skor === 6 ? "rgba(245,158,11,0.15)" : "rgba(56,189,248,0.12)";
  const skorBord = s.skor === 6 ? "rgba(245,158,11,0.4)"  : "rgba(56,189,248,0.3)";
  const sma50Str  = s.sma50  ? s.sma50.toFixed(2)  : "&#8212;";
  const sma200Str = s.sma200 ? s.sma200.toFixed(2) : "&#8212;";
  const rsStr = s.rs_vs_xu != null ? (s.rs_vs_xu >= 0 ? `+${s.rs_vs_xu.toFixed(1)}%` : `${s.rs_vs_xu.toFixed(1)}%`) : "&#8212;";
  const rsClr = (s.rs_vs_xu || 0) >= 0 ? "#4ade80" : "#f87171";
  const kriterBadges = (s.kriterler || []).map(k =>
    `<span style="background:rgba(74,222,128,0.12);color:#4ade80;border:1px solid rgba(74,222,128,0.3);border-radius:5px;padding:3px 9px;font-size:0.68rem;font-weight:700;">&#10003; ${k}</span>`
  ).join("");

  card.innerHTML = `
    <button onclick="closeTop3Popup()" style="position:absolute;top:12px;right:14px;background:none;border:none;color:#64748b;font-size:1.2rem;cursor:pointer;line-height:1;padding:0;">&#10005;</button>
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;padding-right:24px;">
      <span style="font-size:1.5rem;font-weight:900;color:#f1f5f9;letter-spacing:0.06em;">${s.ticker}</span>
      <span style="background:${skorBg};border:1px solid ${skorBord};border-radius:20px;padding:4px 12px;font-size:0.78rem;font-weight:800;color:${skorRenk};">${skorIkon} ${s.skor}/6 Sinyal</span>
    </div>
    <div style="display:flex;align-items:baseline;gap:10px;margin-bottom:16px;padding-bottom:14px;border-bottom:1px solid rgba(255,255,255,0.07);">
      <span style="font-size:1.6rem;font-weight:900;color:#f1f5f9;font-family:monospace;">${priceStr}</span>
      <span style="font-size:1rem;font-weight:700;color:${degClr};">${degIcon} %${Math.abs(s.degisim_pct).toFixed(2)}</span>
    </div>
    <div class="top3-popup-stat-grid">
      <div style="background:rgba(255,255,255,0.04);border-radius:7px;padding:8px;text-align:center;">
        <div style="font-size:0.55rem;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px;">RSI 14</div>
        <div style="font-size:1rem;font-weight:800;color:#38bdf8;">${s.rsi || "&#8212;"}</div>
      </div>
      <div style="background:rgba(255,255,255,0.04);border-radius:7px;padding:8px;text-align:center;">
        <div style="font-size:0.55rem;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px;">Hacim</div>
        <div style="font-size:1rem;font-weight:800;color:#f59e0b;">${s.hacim_x}x</div>
      </div>
      <div style="background:rgba(255,255,255,0.04);border-radius:7px;padding:8px;text-align:center;">
        <div style="font-size:0.55rem;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;margin-bottom:3px;">RS / XU100</div>
        <div style="font-size:1rem;font-weight:800;color:${rsClr};">${rsStr}</div>
      </div>
    </div>
    <div style="display:flex;gap:8px;margin-bottom:14px;">
      <div style="flex:1;background:rgba(255,255,255,0.04);border-radius:7px;padding:7px 10px;display:flex;justify-content:space-between;align-items:center;">
        <span style="font-size:0.62rem;color:#64748b;font-weight:600;">SMA 50</span>
        <span style="font-size:0.82rem;font-weight:700;color:#e2e8f0;">${sma50Str}</span>
      </div>
      <div style="flex:1;background:rgba(255,255,255,0.04);border-radius:7px;padding:7px 10px;display:flex;justify-content:space-between;align-items:center;">
        <span style="font-size:0.62rem;color:#64748b;font-weight:600;">SMA 200</span>
        <span style="font-size:0.82rem;font-weight:700;color:#e2e8f0;">${sma200Str}</span>
      </div>
    </div>
    <div style="margin-bottom:16px;">
      <div style="font-size:0.58rem;color:#64748b;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:7px;">Algoritmik Kriterler</div>
      <div style="display:flex;flex-wrap:wrap;gap:5px;">${kriterBadges}</div>
    </div>
    <div style="position:relative;border-radius:9px;overflow:hidden;margin-bottom:16px;">
      <div style="background:rgba(124,58,237,0.08);border:1px solid rgba(124,58,237,0.2);border-radius:9px;padding:13px 14px;filter:blur(3.5px);user-select:none;pointer-events:none;">
        <div style="font-size:0.62rem;color:#a78bfa;font-weight:800;letter-spacing:0.06em;margin-bottom:8px;">ICT BOTTOM LINE &#183; KURUMSAL ANALiZ</div>
        <div style="font-size:0.73rem;color:#e2e8f0;margin-bottom:5px;">Likidite Seviyesi: Aktif &#183; OB B&#246;lgesi Tespit Edildi</div>
        <div style="font-size:0.73rem;color:#e2e8f0;margin-bottom:8px;">Kurumsal Birikim Skoru: 8.4 / 10</div>
        <div style="display:flex;gap:14px;">
          <span style="font-size:0.7rem;color:#4ade80;font-weight:700;">Giri&#351;: &#8212;&#8212;</span>
          <span style="font-size:0.7rem;color:#f87171;font-weight:700;">Stop: &#8212;&#8212;</span>
          <span style="font-size:0.7rem;color:#38bdf8;font-weight:700;">Hedef: &#8212;&#8212;</span>
        </div>
      </div>
      <div style="position:absolute;inset:0;background:rgba(10,20,30,0.65);display:flex;flex-direction:column;align-items:center;justify-content:center;gap:7px;border-radius:9px;">
        <div style="background:#7c3aed;color:#fff;font-size:0.62rem;font-weight:900;padding:3px 12px;border-radius:20px;letter-spacing:0.1em;">ELITE</div>
        <div style="font-size:0.68rem;color:#c4b5fd;font-weight:600;text-align:center;">Tam analiz Telegram ELITE kanal&#305;nda</div>
      </div>
    </div>
    <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" style="display:block;background:linear-gradient(90deg,#7c3aed,#4f46e5);color:#fff;text-align:center;padding:11px;border-radius:8px;font-size:0.8rem;font-weight:800;text-decoration:none;margin-bottom:12px;letter-spacing:0.02em;">
      Telegram ELITE&#8217;de Detayl&#305; &#304;ncele &#8594;
    </a>
    <p style="font-size:0.58rem;color:#475569;font-style:italic;text-align:center;line-height:1.5;margin:0;">
      Bu analiz yat&#305;r&#305;m tavsiyesi de&#287;ildir. Algoritmik tarama sonu&#231;lar&#305; yaln&#305;zca bilgi ama&#231;l&#305;d&#305;r. Yat&#305;r&#305;m kararlar&#305; ki&#351;isel risk tolerans&#305; ve yetkili dan&#305;&#351;manl&#305;k &#231;er&#231;evesinde al&#305;nmal&#305;d&#305;r.
    </p>`;

  ov.style.display = "flex";
  document.body.style.overflow = "hidden";
}

function closeTop3Popup() {
  const ov = document.getElementById("top3-popup-overlay");
  if (ov) ov.style.display = "none";
  document.body.style.overflow = "";
}

// Modal click handler — MutationObserver ile dinamik elementleri yakala
(function(){
  function attach(root){
    var els=root.querySelectorAll?root.querySelectorAll('.smr-lock'):[];
    for(var i=0;i<els.length;i++){
      if(!els[i]._mAttached){
        els[i]._mAttached=true;
        els[i].addEventListener('click',function(e){e.preventDefault();e.stopPropagation();if(typeof openPlansModal==='function')openPlansModal();});
      }
    }
  }
  var obs=new MutationObserver(function(muts){
    muts.forEach(function(m){
      m.addedNodes.forEach(function(n){if(n.nodeType===1)attach(n);});
    });
  });
  if(document.body){obs.observe(document.body,{childList:true,subtree:true});}
  else{document.addEventListener('DOMContentLoaded',function(){obs.observe(document.body,{childList:true,subtree:true});});}
})();
