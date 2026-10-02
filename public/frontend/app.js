/**
 * app.js — SMR Public Vitrin v2
 * Patron Terminal tasarımına uygun 3-kolon layout + grafikler.
 */

const JSON_URL        = "./latest.json";
const TG_PRO_URL      = "#planlar";
const TG_ELITE_URL    = "#planlar";
const TWITTER_URL     = "https://x.com/SMRadar_2026";
const AUTO_REFRESH_MS = 5 * 60 * 1000;   // 5 dakika
// Kilitli kutularda ASLA örnek/uydurma rakam gösterilmez — sadece bu maske (1 Eki 2026).
const KILIT_MASK = '<span style="letter-spacing:2px">🔒 ••••</span>';

let _lastDataStamp = null;

document.addEventListener("DOMContentLoaded", async () => {
  await davetIsle();
  try {
    await loadData();
    if (window._acilisHisse) hizliAc(window._acilisHisse);
    onYukle();
  } catch (e) {
    showError("Veri yüklenemedi. Lütfen daha sonra tekrar deneyin.");
    console.error(e);
  }
});

async function loadData() {
  const res = await fetch(JSON_URL + "?t=" + Date.now());
  if (!res.ok) throw new Error("JSON fetch failed: " + res.status);
  const data = await res.json();

  _lastDataStamp = (data.meta?.canli_guncelleme || '') + '|' + (data.meta?.tarih || '') + '|' + (data.meta?.guncelleme || '');

  hideLoading();
  startAutoRefresh();   // renderAll'dan ÖNCE — render hatası olsa bile interval başlar
  renderAll(data);
}

function renderAll(data) {
  window._sonVeri = data;
  if (!window._hakIlk) { window._hakIlk = true; hakKontrol("XU100"); }   // admin rozeti için (sayılmaz)
  renderHizliBar();
  renderTerazi();
  renderGucluAkis();
  renderEndekstenGuclu();
  renderBugunOlanlar();
  renderPiyasaNabzi();
  renderUpdateTime(data.meta);
  renderTop3Sinyaller(data.top3_sinyaller || []);
  if (window._seciliHisse) {
    window.loadTicker(window._seciliHisse, true);   // otomatik yenileme seçili hisseyi ezmez
  } else {
    renderXU100Panel(data.xu100, data.piyasa_ozeti, data.xu100_grafik || []);
    renderTeknikSeviyeler(data.xu100);
  }
  if (!window._seciliHisse) renderHacimPanel(data.piyasa_ozeti);   // seçili hissede Akıllı Para kartı
  renderComposite(data.piyasa_ozeti);
  renderICT(data.xu100, data.piyasa_ozeti);
  if (!window._seciliHisse) renderSidebarLeft(data.xu100, data.piyasa_ozeti);   // seçili hissede loadTicker çizer
  renderSidebarRight(data.xu100, data.piyasa_ozeti);
  renderCanliSinyaller(data.xu100, data.piyasa_ozeti);
  renderOneCikanlar(data.piyasa_ozeti);
  renderKurumsalPanel(data.xu100);
  renderTeknikYolPanel(data.piyasa_ozeti);
  renderRadarPanel();
  renderTgAdPanel();
  renderCTA();
  renderUserCounter(data.meta);
  updateTwitterLinks();
  renderBgDeco(data.xu100_grafik || []);
  renderErkenRadarPreview();
}

// ── ÜCRETSİZ HAK + ADMIN (2 Eki 2026) ────────────────────────────────────────
// Kural (kullanıcı kararı): ücretsiz ziyaretçi XU100'ü her zaman, ek olarak 24 saatte 1
// BIST100 hissesini görür (aynı hisse gün içinde serbest). Admin (gizli anahtarla /api/admin
// girişi → HttpOnly çerez) her şeyi görür. Sayım sunucuda: free_gate.py /api/free-check.
// Servis cevap vermezse site bozulmasın diye kapı AÇIK kalır (yumuşak kilit; gerçek dosya
// koruması abonelik aşamasında nginx kapısıyla gelecek).
const SMR_API = (location.hostname === "localhost" || location.hostname === "127.0.0.1") ? "http://localhost:8090" : "";
window._smrAdmin = false;
async function hakKontrol(t) {
  try {
    const r = await fetch(`${SMR_API}/api/free-check?ticker=${encodeURIComponent(t)}`, { credentials: "include" });
    if (!r.ok) return { allowed: true };
    const j = await r.json();
    window._smrAdmin = !!j.admin;
    adminRozeti();
    if (j.yeni_bonus > 0) bonusToast(j);
    return j;
  } catch (e) { return { allowed: true }; }
}
// Bugün açılmış hisseler tarayıcıda da tutulur → aynı hisse tekrar açılınca sunucu cevabı beklenmez.
// (Sadece hız içindir; hakkı sunucu sayar, bu liste hak vermez.)
function hakYerel() {
  try {
    const o = JSON.parse(localStorage.getItem("smr_hak") || "{}");
    return (o.ts && Date.now() - o.ts < 20 * 3600e3 && Array.isArray(o.t)) ? o.t : [];
  } catch (e) { return []; }
}
function hakYerelEkle(t) {
  try {
    const o = JSON.parse(localStorage.getItem("smr_hak") || "{}");
    const taze = o.ts && Date.now() - o.ts < 20 * 3600e3;
    const l = taze && Array.isArray(o.t) ? o.t : [];
    if (!l.includes(t)) l.push(t);
    localStorage.setItem("smr_hak", JSON.stringify({ ts: taze ? o.ts : Date.now(), t: l }));
  } catch (e) {}
}
function adminRozeti() {
  const st = document.getElementById("sel-stock-go");
  if (!st || document.getElementById("admin-rozet")) return;
  if (!window._smrAdmin) return;
  const b = document.createElement("span");
  b.id = "admin-rozet";
  b.textContent = "ADMIN · sınırsız";
  b.style.cssText = "align-self:flex-end;margin-bottom:9px;font-size:10px;font-weight:800;letter-spacing:1px;padding:3px 8px;border-radius:4px;background:rgba(245,158,11,.15);border:1px solid #f59e0b;color:#fbbf24";
  st.insertAdjacentElement("afterend", b);
}
function hakDoldu(t, h) {
  const st = document.getElementById("sel-status");
  if (!st) return;
  const sa = Math.floor((h.kalan_dk || 0) / 60), dk = (h.kalan_dk || 0) % 60;
  const kalan = sa ? `${sa} sa ${dk} dk` : `${dk} dk`;
  const hisse = h.hak_hisse
    ? ` Bugünkü hissen: <a href="#" onclick="(function(){var i=document.getElementById('sel-stock-input');if(i)i.value='${h.hak_hisse}';window.loadTicker('${h.hak_hisse}');})();return false;" style="color:#38bdf8;font-weight:700">${h.hak_hisse}</a>.`
    : "";
  const hak = h.hak || 1;
  st.innerHTML = `<span style="color:#f59e0b">🔒 Bugünkü ücretsiz hakkın doldu (${hak} hisse).${hisse}
    ${kalan} sonra yeni hisse seçebilirsin.</span>
    <a href="#" onclick="paylas();return false;" style="display:block;margin:6px 0 2px;color:#7dd3fc;font-weight:700">𝕏 Analizini paylaş, linkinden biri gelirse +1 hisse açılır →</a>
    <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;"
       style="color:#a78bfa;font-weight:700;margin-left:4px">Tüm hisseler PRO / ELITE'te →</a>`;
  st.scrollIntoView({ behavior: "smooth", block: "center" });
}

// ── SİTEDE KALMA SÜRESİ (2 Eki 2026) ─────────────────────────────────────────
// Sadece sayfanın EKRANDA olduğu süre sayılır; sayfa gizlenince/kapanınca toplam süre
// tek bir küçük istekle gönderilir (/api/sure). Kişisel bilgi yok: rastgele ziyaret kimliği + saniye.
(function() {
  const vid = Math.random().toString(36).slice(2, 12);
  let toplam = 0, bas = document.visibilityState === "visible" ? Date.now() : null;
  const gonder = () => {
    if (bas) { toplam += Date.now() - bas; bas = null; }
    const sn = Math.round(toplam / 1000);
    if (sn < 2 || !navigator.sendBeacon) return;
    try { navigator.sendBeacon(`${SMR_API}/api/sure`, new Blob([`v=${vid}&s=${sn}`], { type: "application/x-www-form-urlencoded" })); } catch (e) {}
  };
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") bas = Date.now(); else gonder();
  });
  window.addEventListener("pagehide", gonder);
})();

// ── PAYLAŞ → +1 HİSSE (2 Eki 2026) ───────────────────────────────────────────
// Ziyaretçi analizi X'te paylaşır; linkinde kendine özel kısa kod (d=) var. O linkle
// sitede İLK KEZ görülen biri gelirse (farklı bağlantıdan) paylaşana +1 hisse hakkı
// (24 saatte en fazla 3). Sayım sunucuda: free_gate.py /api/davet-kodu + /api/davet.
const SITE_ADRES = "smartmoneyradar.app";
async function davetIsle() {
  let q;
  try { q = new URLSearchParams(location.search); } catch (e) { return; }
  const h = (q.get("h") || "").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 10);
  if (h && h !== "XU100") window._acilisHisse = h;
  const d = (q.get("d") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 12);
  if (d) {
    try { await fetch(`${SMR_API}/api/davet?d=${encodeURIComponent(d)}`, { credentials: "include" }); } catch (e) {}
    try { q.delete("d"); history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q : "")); } catch (e) {}
  }
  davetKoduAl();
}
async function davetKoduAl() {
  if (window._davetKod) return window._davetKod;
  try {
    const r = await fetch(`${SMR_API}/api/davet-kodu`, { credentials: "include" });
    if (r.ok) window._davetKod = (await r.json()).kod || "";
  } catch (e) {}
  return window._davetKod || "";
}
function sayiTR(x) { return Math.abs(x).toFixed(1).replace(".", ","); }
function paylasMetni() {
  const t = aktifTicker();
  const j = window._seciliJson;
  // /p/<T>.html: X bu sayfadan grafik kartını (kart/<T>.png) alır, insanı siteye yönlendirir.
  const link = `${SITE_ADRES}/p/${t}.html` + (window._davetKod ? `?d=${window._davetKod}` : "");
  let ilk;
  const g = j && j.akilli ? j.akilli.guc20 : null;
  // XU100: bugünkü değişim + SMA200'e uzaklık (latest.json). Diğer endeks/hisse: XU100'e göre 20 gün.
  const xu = window._sonVeri && window._sonVeri.xu100;
  let ex;
  if (window._seciliHisse && j && g != null && t !== "XU100") {
    ilk = g >= 0 ? `${t} son 20 günde XU100’ü ${sayiTR(g)} puan geçti 📈`
                 : `${t} son 20 günde XU100’ün ${sayiTR(g)} puan gerisinde kaldı 📉`;
  } else if ((ex = t === "XU100" ? xu : (window._seciliHisse && j ? j.hisse : null))
             && ex.degisim_pct != null && ex.kapanis && ex.sma200) {
    const d = ex.degisim_pct, u = (ex.kapanis / ex.sma200 - 1) * 100;
    ilk = `${t} bugün %${Math.abs(d).toFixed(2).replace(".", ",")} ${d >= 0 ? "yükseldi" : "düştü"} ${d >= 0 ? "📈" : "📉"}
`
        + `SMA200’ün %${sayiTR(u)} ${u >= 0 ? "üstünde" : "altında"}. Akıllı para ne diyor?`;
  } else {
    ilk = `${t} bugün nereye gidiyor? Akıllı para ne diyor? 📊`;
  }
  return `${ilk}

Algoritmanın gözünden bak
👉 ${link}`;
}
window.paylas = function() {
  const url = "https://twitter.com/intent/tweet?text=" + encodeURIComponent(paylasMetni());
  window.open(url, "_blank", "noopener");
  if (!window._davetKod) davetKoduAl();
};
function renderPaylasBar() {
  const el = document.getElementById("paylas-bar");
  if (!el) return;
  const t = aktifTicker();
  el.innerHTML = `<a href="#" onclick="paylas();return false;" class="paylas-btn">
    <span style="font-weight:800">𝕏</span>&nbsp; ${t} analizini paylaş
    <span class="paylas-ayrac">·</span><span class="paylas-odul">+1 hisse daha inceleme kazan</span></a>`;
}
function bonusToast(j) {
  const n = j.yeni_bonus || 1;
  const d = document.createElement("div");
  d.className = "bonus-toast";
  d.innerHTML = `🎉 Linkinden ${n} kişi geldi · <strong>+${n} hisse</strong> hakkın var <span style="opacity:.75">(bugün ${j.kazanilan || n}/3)</span>`;
  document.body.appendChild(d);
  setTimeout(() => { d.style.opacity = "0"; }, 6000);
  setTimeout(() => { d.remove(); }, 6800);
}

// ── HIZLI ERİŞİM: ENDEKSLER + TAKİP LİSTEM (2 Eki 2026) ──────────────────────
// Endeks düğmeleri ücretsiz kotaya sayılmaz (loadTicker X* için hak sormaz).
// Takip listesi yalnız ziyaretçinin tarayıcısında (localStorage); üyelik gerekmez.
const ENDEKSLER = ["XU100", "XU030", "XBANK", "XUSIN", "XTUMY"];
const TAKIP_ANAHTAR = "smr_takip";
function takipOku() {
  try { const a = JSON.parse(localStorage.getItem(TAKIP_ANAHTAR) || "[]"); return Array.isArray(a) ? a.slice(0, 12) : []; }
  catch (e) { return []; }
}
function takipYaz(a) { try { localStorage.setItem(TAKIP_ANAHTAR, JSON.stringify(a.slice(0, 12))); } catch (e) {} }
function aktifTicker() { return window._seciliHisse || "XU100"; }
function hizliAc(t) {
  const i = document.getElementById("sel-stock-input");
  if (i) i.value = t;
  window.loadTicker(t).then(r => {
    const st = document.getElementById("sel-status");
    if (st && r === true) st.innerHTML = `<span style="color:#1d9e75;font-weight:600">${t}</span> yüklendi`;
    renderHizliBar();
  });
}
function takipDegistir() {
  const t = aktifTicker();
  let a = takipOku();
  a = a.includes(t) ? a.filter(x => x !== t) : [t, ...a];
  takipYaz(a);
  renderHizliBar();
}
function renderHizliBar() {
  renderPaylasBar();
  const el = document.getElementById("hizli-bar");
  const aktif = aktifTicker();
  const yild = document.getElementById("takip-yildiz");
  const takip = takipOku();
  if (yild) yild.innerHTML = takip.includes(aktif) ? '★<span class="takip-yazi"> Takipte</span>' : '☆<span class="takip-yazi"> Takip</span>';
  if (!el) return;
  const chip = (t, renk) => `<button type="button" onclick="hizliAc('${t}')"
     style="cursor:pointer;min-height:30px;padding:4px 10px;border-radius:14px;font-size:11px;font-weight:600;
            background:${t === aktif ? renk + "33" : "transparent"};border:1px solid ${t === aktif ? renk : "var(--border2)"};color:${t === aktif ? "#fff" : "var(--text-dim)"}">${t}</button>`;
  el.innerHTML = `<span style="color:var(--text-muted);letter-spacing:.5px">ENDEKSLER</span>${ENDEKSLER.map(t => chip(t, "#38bdf8")).join("")}
    <span style="width:1px;height:18px;background:var(--border2);margin:0 4px"></span>
    <span style="color:#fbbf24;letter-spacing:.5px">⭐ TAKİP LİSTEM</span>
    ${takip.length ? takip.map(t => chip(t, "#fbbf24")).join("") : `<span style="color:var(--text-muted)">Panel başlığındaki ☆ ile hisse ekle</span>`}`;
}

// ── SEÇİLİ HİSSE (1 Eki 2026 — site fazı adım 1) ─────────────────────────────
// Üstteki seçici (index.html applyTicker) window.loadTicker(t) çağırır. Hisse dosyası
// hisse/<T>.json — VPS site_hisse_uretici.py seans içi 30 dk'da bir üretir (app.py ile
// aynı hesap). XU100 seçilince latest.json'daki (15 dk) XU100 görünümüne dönülür.
window._seciliHisse = null;
// Hisse dosyası önbelleği: 5 dk içinde tekrar açılırsa ağa gidilmez (dosya 30 dk'da bir yenilenir).
const _hisseOnbellek = {};
function hisseAl(t) {
  const c = _hisseOnbellek[t];
  if (c && Date.now() - c.ts < 5 * 60 * 1000) return c.p;
  const p = fetch(`hisse/${encodeURIComponent(t)}.json?t=${Date.now()}`)
    .then(r => r.ok ? r.json() : null).catch(() => null)
    .then(j => { if (!j) delete _hisseOnbellek[t]; return j; });
  _hisseOnbellek[t] = { ts: Date.now(), p };
  return p;
}
// Sayfa açıldıktan sonra boşta: endeksler + takip listesi önceden indirilir (tıklayınca anında açılır).
// Sadece veri dosyası; günlük hak sayılmaz.
function onYukle() {
  const liste = [...ENDEKSLER.filter(x => x !== "XU100"), ...takipOku()].slice(0, 16);
  const calis = () => liste.forEach((t, i) => setTimeout(() => hisseAl(t), i * 150));
  const bos = () => ("requestIdleCallback" in window) ? requestIdleCallback(calis, { timeout: 4000 }) : setTimeout(calis, 1500);
  if (document.readyState === "complete") bos(); else window.addEventListener("load", bos, { once: true });   // açılışı geciktirmesin
}
window.loadTicker = async function(t, sessiz) {
  t = (t || "").trim().toUpperCase().replace(/\.IS$/, "");
  if (!t) return false;
  const baslik = document.getElementById("panel-ticker");
  if (t === "XU100") {
    window._seciliHisse = null;
    if (baslik) baslik.textContent = "XU100";
    const v = window._sonVeri;
    if (v) { renderXU100Panel(v.xu100, v.piyasa_ozeti, v.xu100_grafik || []); renderTeknikSeviyeler(v.xu100); renderSidebarLeft(v.xu100, v.piyasa_ozeti); renderHacimPanel(v.piyasa_ozeti); }
    renderHizliBar();
    return true;
  }
  // Hak kontrolü ile hisse verisi AYNI ANDA istenir (sırayla değil) → bekleme yarıya iner.
  const veriP = hisseAl(t);
  if (!t.startsWith("X")) {                       // endeksler serbest; hisse → günlük hak
    if (window._smrAdmin || hakYerel().includes(t)) {
      hakKontrol(t);                               // bugün zaten açık → bekleme yok, sayım arkada
    } else {
      const h = await hakKontrol(t);
      if (!h.allowed) { hakDoldu(t, h); return "kilit"; }
      hakYerelEkle(t);
    }
  }
  const j = await veriP;
  if (!j || !j.hisse) return false;
  window._seciliHisse = t;
  window._seciliJson = j;
  if (baslik) baslik.textContent = j.hisse.ticker;
  renderXU100Panel(j.hisse, null, j.grafik || [], {
    label: j.hisse.ticker, isIndex: j.hisse.ticker.startsWith("X"),
    hideSkor: true, fiyatSaati: j.meta?.fiyat_saati,
    // Son bar bugünden eskiyse gösterilen fiyat o günün KAPANIŞI; dosya yazım saati yanıltır.
    fiyatEtiket: (j.meta?.son_bar && j.meta.son_bar < new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10))
      ? `Kapanış fiyatı · <strong style="color:var(--text-dim)">${j.meta.son_bar.slice(5).split("-").reverse().join(".")}</strong>`
      : null,
  });
  renderTeknikSeviyeler(j.hisse, j.hisse.ticker);
  renderSidebarLeft(j.hisse, window._sonVeri?.piyasa_ozeti, { label: j.hisse.ticker });
  if (!renderHisseAkilliPara(j)) renderHacimPanel(window._sonVeri?.piyasa_ozeti);
  renderHizliBar();
  if (!sessiz) document.getElementById("xu100-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
  return true;
};
window.xu100eDon = function() {
  const inp = document.getElementById("sel-stock-input");
  if (inp) inp.value = "XU100";
  window.loadTicker("XU100");
  const st = document.getElementById("sel-status");
  if (st) st.textContent = "";
};

// ── KANIT TERAZİSİ şeridi (1 Eki 2026 — site fazı adım 3) ─────────────────────
// terazi_xu100.json: VPS site_terazi.py, app.py'nin kendi _compute_kanit_ozeti'si.
async function renderTerazi() {
  const el = document.getElementById("terazi-serit");
  if (!el) return;
  let t;
  try {
    const r = await fetch("terazi_xu100.json?t=" + Date.now());
    if (!r.ok) { el.innerHTML = ""; return; }
    t = await r.json();
  } catch (e) { el.innerHTML = ""; return; }
  if (!t || !t.etiket) { el.innerHTML = ""; return; }
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const oy = (t.oylar || []).slice(0, 3).map(o =>
    `<div style="display:flex;gap:6px;align-items:baseline;font-size:11.5px;line-height:1.5">
       <span style="color:${o.yon === "boga" ? "#22c55e" : o.yon === "ayi" ? "#f87171" : "#94a3b8"};font-weight:700">${o.yon === "boga" ? "▲" : o.yon === "ayi" ? "▼" : "•"}</span>
       <span><strong style="color:var(--text)">${esc(o.ad)}</strong> <span style="color:var(--text-dim)">— ${esc(o.neden)}</span></span>
     </div>`).join("");
  const not = t.tek_sinyal
    ? `<span style="color:#f59e0b">Tek sinyal — hüküm için zayıf, farklı bir kanıttan teyit beklenir.</span>`
    : (t.celiski ? `<span style="color:#f59e0b">Kanıtlar çelişkili.</span>` : "");
  el.innerHTML = `
    <div class="panel" style="padding:10px 14px;margin-bottom:12px;border-left:3px solid ${t.renk}">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <span style="font-size:10px;letter-spacing:1.2px;color:var(--text-muted);font-weight:700">⚖ KANIT TERAZİSİ · XU100</span>
        <span style="font-size:15px;font-weight:800;color:${t.renk}">${esc(t.etiket)}</span>
        <span style="font-size:12px;font-family:'JetBrains Mono',monospace">
          <span style="color:#22c55e">✓${t.boga_oy}</span>&nbsp;<span style="color:#f87171">✗${t.ayi_oy}</span></span>
        <span style="margin-left:auto;font-size:10px;color:var(--text-muted)">güncellendi ${esc((t.uretim || "").slice(11))}</span>
      </div>
      ${oy ? `<div style="margin-top:6px">${oy}</div>` : ""}
      ${not ? `<div style="margin-top:4px;font-size:11px">${not}</div>` : ""}
      ${t.sok_serit ? `<div style="margin-top:4px;font-size:11px;color:#fbbf24">${esc(t.sok_serit)}</div>` : ""}
    </div>`;
}

// ── GÜÇLÜ AKIŞ kutusu (1 Eki 2026 — site fazı adım 4) ─────────────────────────
// yildiz_ozet.json: VPS yildiz_masterscan_telegram.py, PRO mesajı GİTTİKTEN sonra yazar.
// Dosyada sadece grup sayıları + listenin 1. hissesi var; diğer isimler siteye hiç gelmez.
async function renderGucluAkis() {
  const el = document.getElementById("guclu-akis-kutu");
  if (!el) return;
  let y;
  try {
    const r = await fetch("yildiz_ozet.json?t=" + Date.now());
    if (!r.ok) { el.innerHTML = ""; el.style.display = "none"; return; }
    y = await r.json();
  } catch (e) { el.innerHTML = ""; el.style.display = "none"; return; }
  if (!y || !Array.isArray(y.gruplar)) { el.style.display = "none"; return; }
  el.style.display = "";
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const tarih = (y.as_of || "").split("-").reverse().join(".");
  const i = y.ilk;
  const ilkHtml = i ? `
    <div style="background:rgba(56,189,248,0.07);border:1px solid rgba(56,189,248,0.35);border-radius:6px;padding:8px 10px;margin:6px 0 8px">
      <div style="font-size:9px;letter-spacing:1px;color:var(--text-muted)">LİSTENİN 1. HİSSESİ · ${esc(i.grup)}</div>
      <div style="display:flex;align-items:baseline;gap:8px;margin-top:2px">
        <span style="font-size:17px;font-weight:800;color:var(--text)">${esc(i.t)}</span>
        <span style="font-family:'JetBrains Mono',monospace;font-size:12px">${fmt(i.fiyat)}</span>
        <span style="font-size:11px;color:${i.degisim >= 0 ? "#22c55e" : "#f87171"}">${i.degisim >= 0 ? "▲" : "▼"} %${Math.abs(i.degisim).toFixed(1)}</span>
      </div>
      <div style="font-size:10px;color:var(--text-dim)">52 hafta aralığında %${Math.round(i.pos52)} konumda</div>
    </div>` : `<div style="font-size:11px;color:var(--text-dim);margin:6px 0">Bu akşam listede hisse yok.</div>`;
  const gr = y.gruplar.map(g => {
    const kilit = Math.max(0, g.adet - (i && i.grup === g.label ? 1 : 0));
    const satir = Array.from({ length: Math.min(kilit, 3) }, () =>
      `<div style="font-size:10.5px;color:var(--text-muted);letter-spacing:2px">🔒 ••••• &nbsp;•••</div>`).join("");
    return `<div style="margin-top:6px">
      <div style="display:flex;justify-content:space-between;font-size:11px">
        <span style="color:var(--text);font-weight:700">${esc(g.label)}</span>
        <span style="color:#38bdf8;font-weight:700">${g.adet}</span></div>
      <div style="font-size:9.5px;color:var(--text-muted);line-height:1.35;margin-bottom:2px">${esc(g.aciklama)}</div>
      ${satir}${kilit > 3 ? `<div style="font-size:10px;color:var(--text-muted)">+${kilit - 3} hisse daha</div>` : ""}
    </div>`;
  }).join("");
  el.innerHTML = `
    <div class="rsection-title" style="color:#fbbf24">⭐ Güçlü Akış · Yıldız Pazar</div>
    <div style="font-size:9.5px;color:var(--text-dim)">${tarih} akşam listesi · ${y.toplam} hisse</div>
    ${ilkHtml}
    ${gr}
    <div style="margin-top:10px;font-size:10.5px;color:#fbbf24;line-height:1.4">Tam liste her akşam 22:30'da Telegram PRO kanalında.</div>
    <div style="margin-top:4px;font-size:9px;color:var(--text-muted)">İşlem sinyali değildir.</div>`;
}

// hisse/_liste.json tek sefer çekilir; 60 sn içindeki tüm kullanıcılar aynı cevabı paylaşır
// (index.html'deki hisse listesi dahil). Otomatik yenilemede taze çekilir.
function listeAl() {
  const simdi = Date.now();
  if (window._listeP && simdi - (window._listeT || 0) < 60000) return window._listeP;
  window._listeT = simdi;
  window._listeP = fetch("hisse/_liste.json?t=" + simdi).then(r => r.ok ? r.json() : null).catch(() => null);
  return window._listeP;
}

// ── PİYASA NABZI (2 Eki 2026 — sol sütun) ─────────────────────────────────────
// hisse/_liste.json → piyasa: BIST100 yükselen/düşen, en çok yükselen/düşen 3, genişlik.
// VPS site_hisse_uretici.py ile aynı turda (seans içi 30 dk). Dosya yoksa latest.json'daki
// günlük piyasa özetine (SMA200 üstü %) düşer.
async function renderPiyasaNabzi() {
  const el = document.getElementById("piyasa-nabzi");
  if (!el) return;
  let p = null, uretim = "";
  try {
    const j = await listeAl();
    if (j) { p = j.piyasa; uretim = j.uretim || ""; }
  } catch (e) { /* yedeğe düş */ }
  const esc = x => String(x ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const yuz = v => `%${Math.round(v)}`;
  const renk = v => v >= 50 ? "#22c55e" : v >= 30 ? "#f59e0b" : "#f87171";
  const satir = (lbl, v) => `<div style="display:flex;justify-content:space-between;font-size:11px;line-height:1.7">
      <span style="color:var(--text-dim)">${lbl}</span><strong style="color:${renk(v)}">${yuz(v)}</strong></div>`;

  if (!p || p.yukselen == null) {
    const o = window._sonVeri?.piyasa_ozeti;
    if (!o || o.sma200_ustu_pct == null) { el.innerHTML = ""; return; }
    el.innerHTML = `<div class="sidebar-section-title" style="color:#38bdf8">💓 Piyasa Nabzı</div>
      ${satir("SMA200 üstü hisse", o.sma200_ustu_pct)}
      <div style="font-size:9px;color:var(--text-muted)">Günlük özet · 150 hisse</div>`;
    return;
  }
  const top = p.yukselen + p.dusen + (p.yatay || 0) || 1;
  const yuzY = p.yukselen / top * 100, yuzD = p.dusen / top * 100;
  const bugun = new Date(Date.now() + 3 * 3600e3).toISOString().slice(0, 10);   // TR tarihi
  const gunEtiket = (p.gun === bugun ? "Bugün" : `Son kapanış (${(p.gun || "").slice(5).split("-").reverse().join(".")})`)
    + (p.kapsanan && p.kapsanan < 100 ? ` · ${p.kapsanan} hissenin bugünkü verisi geldi` : "");
  const hisse = (h, yon) => `<div onclick="(function(){var i=document.getElementById('sel-stock-input');if(i)i.value='${esc(h.t)}';window.loadTicker('${esc(h.t)}');})()"
      style="display:flex;justify-content:space-between;font-size:11px;line-height:1.65;cursor:pointer">
      <span style="color:var(--text)">${esc(h.t)}</span>
      <span style="color:${yon > 0 ? "#22c55e" : "#f87171"};font-family:'JetBrains Mono',monospace">${h.degisim >= 0 ? "+" : "−"}%${Math.abs(h.degisim).toFixed(1).replace(".", ",")}</span></div>`;
  el.innerHTML = `
    <div class="sidebar-section-title" style="color:#38bdf8">💓 Piyasa Nabzı · BIST100</div>
    <div style="font-size:9.5px;color:var(--text-muted);margin-bottom:6px">${gunEtiket}</div>
    <div style="display:flex;justify-content:space-between;font-size:11.5px;font-weight:700">
      <span style="color:#22c55e">▲ ${p.yukselen} yükselen</span><span style="color:#f87171">${p.dusen} düşen ▼</span></div>
    <div style="display:flex;height:6px;border-radius:3px;overflow:hidden;margin:4px 0 8px;background:#334155">
      <div style="width:${yuzY}%;background:#22c55e"></div><div style="width:${100 - yuzY - yuzD}%;background:#64748b"></div><div style="width:${yuzD}%;background:#f87171"></div></div>
    <div style="font-size:9.5px;color:var(--text-muted);letter-spacing:.5px">EN ÇOK YÜKSELEN</div>
    ${(p.en_cok_yukselen || []).map(h => hisse(h, 1)).join("")}
    <div style="font-size:9.5px;color:var(--text-muted);letter-spacing:.5px;margin-top:6px">EN ÇOK DÜŞEN</div>
    ${(p.en_cok_dusen || []).map(h => hisse(h, -1)).join("")}
    <div style="margin-top:8px;padding-top:6px;border-top:1px solid var(--border)">
      ${satir("SMA200 üstü", p.sma200_ustu_pct)}${satir("SMA50 üstü", p.sma50_ustu_pct)}${satir("RSI 50 üstü", p.rsi50_ustu_pct)}
    </div>
    <div style="font-size:9px;color:var(--text-muted);margin-top:4px">güncellendi ${esc(uretim.slice(11))} · hisseye tıkla, analizi açılsın</div>`;
}

// ── BUGÜN OLANLAR (2 Eki 2026 — sol sütun; telefonda ana grafiğin altı) ─────────
// hisse/_liste.json → piyasa.bugun (site_hisse_uretici): SMA200'ü kesenler, 52H zirveye en
// yakın 5, ortalamasının 2 katından fazla işlem görenler. Ölçüm, tahmin/öneri değil.
// Kesiş/zirve gün içinde değişebilir ("ŞU AN"); hacim son tamamlanmış günün ("KESİN").
window._boSekme = "kesis";
function bugunYerlestir() {
  const el = document.getElementById("bugun-olanlar");
  if (!el) return;
  const mobil = window.matchMedia("(max-width: 960px)").matches;
  const hedef = mobil ? document.getElementById("xu100-panel") : document.getElementById("piyasa-nabzi");
  if (hedef && hedef.nextElementSibling !== el) hedef.insertAdjacentElement("afterend", el);
}
try { window.matchMedia("(max-width: 960px)").addEventListener("change", bugunYerlestir); } catch (e) {}
window.boSekme = function(s) { window._boSekme = s; renderBugunOlanlar(); };
async function renderBugunOlanlar() {
  const el = document.getElementById("bugun-olanlar");
  if (!el) return;
  bugunYerlestir();
  let j = null;
  try { j = await listeAl(); } catch (e) {}
  const b = j && j.piyasa && j.piyasa.bugun;
  if (!b) { el.innerHTML = ""; return; }
  const esc = x => String(x ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const tr = (x, n = 1) => Math.abs(x || 0).toFixed(n).replace(".", ",");
  const ac = t => `onclick="hizliAc('${esc(t)}')"`;
  const s = window._boSekme;
  const saat = (j.uretim || "").slice(11, 16);
  const hg = (b.hacim_gun || "").slice(5).split("-").reverse().join(".");
  const rozet = s === "hacim"
    ? `<span class="bo-rozet" style="background:rgba(148,163,184,.1);color:#94a3b8;border:1px solid #334155">KESİN · ${esc(hg)}</span>`
    : `<span class="bo-rozet" style="background:rgba(34,197,94,.12);color:#22c55e;border:1px solid rgba(34,197,94,.35)">ŞU AN · ${esc(saat)}</span>`;
  let ic = "";
  if (s === "kesis") {
    const yuk = b.yukari || [], dus = b.asagi || [];
    const sat = (h, yon) => `<div class="bo-satir" ${ac(h.t)}><b>${esc(h.t)}</b><span class="ac">SMA200</span><span class="d" style="color:${yon > 0 ? "#22c55e" : "#f87171"}">${yon > 0 ? "+" : "−"}%${tr(h.uzak)}</span></div>`;
    ic = `<div class="bo-grup" style="color:#22c55e">▲ SMA200 ÜSTÜNE ÇIKTI · ${yuk.length}</div>
      ${yuk.length ? yuk.map(h => sat(h, 1)).join("") : `<div class="bo-bos">Bugün çıkan yok</div>`}
      <div class="bo-grup" style="color:#f87171">▼ SMA200 ALTINA DÜŞTÜ · ${dus.length}</div>
      ${dus.length ? dus.map(h => sat(h, -1)).join("") : `<div class="bo-bos">Bugün düşen yok</div>`}
      <div class="bo-not">Gün içinde kesiş geri dönebilir; akşam kesinleşir. Hisseye tıkla → analizi açılır.</div>`;
  } else if (s === "zirve") {
    ic = `<div class="bo-alt">52 haftalık zirvesine en yakın 5 hisse (kalan mesafe)</div>
      ${(b.zirve || []).map(h => `<div class="bo-satir" ${ac(h.t)}><b>${esc(h.t)}</b>
        <span class="ac" style="${h.yeni ? "color:#fbbf24;font-weight:800" : ""}">${h.yeni ? "🏔 yeni zirve" : "zirveye"}</span>
        <span class="d" style="color:${h.uzak > -3 ? "#22c55e" : "#fbbf24"}">${h.uzak >= 0 ? "zirvede" : `−%${tr(h.uzak)}`}</span></div>`).join("")}
      <div class="bo-not">Bugün zirvesini aşan en üstte “yeni zirve” diye çıkar.</div>`;
  } else {
    const L = b.hacim || [];
    const enb = Math.max(...L.map(h => h.kat || 0), 1);
    ic = `<div class="bo-alt">Ortalamasının 2 katından (2x) fazla işlem görenler</div>
      ${L.length ? L.map(h => `<div class="bo-satir" ${ac(h.t)}><b>${esc(h.t)}</b>
        <div class="bo-bar"><i style="width:${Math.round((h.kat || 0) / enb * 100)}%"></i></div>
        <span class="d" style="color:#38bdf8;flex:1">${tr(h.kat)}x</span>
        <span class="d" style="color:${h.degisim >= 0 ? "#22c55e" : "#f87171"}">${h.degisim >= 0 ? "+" : "−"}%${tr(h.degisim)}</span></div>`).join("")
        : `<div class="bo-bos">O gün hacmi patlayan olmadı</div>`}
      <div class="bo-not">Gün bitmeden hacim yanıltır; bu yüzden son tamamlanan gün gösterilir.</div>`;
  }
  const btn = (k, ad) => `<button type="button" class="${s === k ? "on" : ""}" onclick="boSekme('${k}')">${ad}</button>`;
  el.innerHTML = `<div class="sidebar-section-title" style="color:#fbbf24;border-left-color:#fbbf24">⚡ Bugün Olanlar · BIST100</div>
    <div class="bo-ic">
      <div style="display:flex;margin-bottom:6px">${rozet}</div>
      <div class="bo-sekme">${btn("kesis", "Kesiş")}${btn("zirve", "Zirve")}${btn("hacim", "Hacim")}</div>
      ${ic}
    </div>`;
}

// ── ENDEKSTEN GÜÇLÜ (2 Eki 2026 — sağ sütun) ─────────────────────────────────
// hisse/_liste.json → piyasa.endeksten_guclu: son 20 işlem gününde XU100'ü en çok geçen
// 5 BIST100 hissesi. Ölçüm (olmuş), tahmin/öneri değil.
async function renderEndekstenGuclu() {
  const el = document.getElementById("endeksten-guclu");
  if (!el) return;
  let p = null;
  try { const j = await listeAl(); if (j) p = j.piyasa; } catch (e) {}
  const L = p && p.endeksten_guclu;
  if (!L || !L.length) { el.innerHTML = ""; return; }
  const esc = x => String(x ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
  const enb = Math.max(...L.map(x => Math.abs(x.puan))) || 1;
  el.innerHTML = `
    <div class="rsection-title" style="color:#22c55e">📈 Endeksten Güçlü · BIST100</div>
    <div style="font-size:9.5px;color:var(--text-dim);margin-bottom:6px">Son 20 günde XU100'ü en çok geçenler</div>
    ${L.map((x, i) => `<div onclick="hizliAc('${esc(x.t)}')" style="cursor:pointer;padding:4px 0">
        <div style="display:flex;justify-content:space-between;font-size:11.5px">
          <span style="color:var(--text)"><span style="color:var(--text-muted)">${i + 1}.</span> ${esc(x.t)}</span>
          <strong style="color:${x.puan >= 0 ? "#22c55e" : "#f87171"}">${x.puan >= 0 ? "+" : "−"}${Math.abs(x.puan).toFixed(1).replace(".", ",")} puan</strong></div>
        <div style="height:3px;border-radius:2px;background:#1e293b;margin-top:3px">
          <div style="height:3px;border-radius:2px;width:${Math.abs(x.puan) / enb * 100}%;background:#22c55e99"></div></div>
      </div>`).join("")}
    <div style="margin-top:6px;font-size:9px;color:var(--text-muted)">Puan = hisse getirisi − XU100 getirisi. Geçmiş ölçüm, öneri değildir.</div>`;
}

function startAutoRefresh() {
  setInterval(async () => {
    try {
      const res = await fetch(JSON_URL + "?t=" + Date.now());
      if (!res.ok) return;
      const data = await res.json();
      const stamp = (data.meta?.canli_guncelleme || '') + '|' + (data.meta?.tarih || '') + '|' + (data.meta?.guncelleme || '');
      if (stamp && stamp !== _lastDataStamp) {
        _lastDataStamp = stamp;
        renderAll(data);
      }
    } catch (e) { /* sessiz başarısızlık */ }
  }, AUTO_REFRESH_MS);
}

// ── ERKEN RADAR PREVIEW (Tadımlık — FREE) ────────────────────────────────────
async function renderErkenRadarPreview() {
  const panel = document.getElementById('erken-radar-panel');
  const body  = document.getElementById('erken-radar-body');
  const tag   = document.getElementById('erken-radar-tag');
  if (!panel || !body) return;
  try {
    const res = await fetch('erken_radar_preview.json?t=' + Date.now(), { cache: 'no-store' });
    if (!res.ok) return; // sessizce gizli kalır
    const data = await res.json();
    window._erkenRadarData = data;          // arama için global
    const items = data.public_items || [];
    if (items.length === 0) return;
    panel.style.display = '';
    if (tag) tag.textContent = `Güncelleme: ${data.generated_at || '—'}`;
    const catIcons = { A: '🔄', B: '📐', C: '🚀', D: '⚠' };
    const itemsHtml = items.map(it => {
      const icon = catIcons[it.category] || '🎯';
      const aging = it.aging_days >= 5
        ? `<span style="color:#fbbf24;font-weight:700;">⏳ ${it.aging_days}g aktif</span>`
        : (it.aging_days >= 2 ? `<span style="color:#94a3b8;">${it.aging_days}g aktif</span>` : '');
      return `<div class="erp-row">
        <div class="erp-row-line">
          <span class="erp-icon">${icon}</span>
          <span class="erp-ticker">${it.ticker}</span>
          <span class="erp-stars">★★★★★</span>
          ${aging ? `<span class="erp-aging">${aging}</span>` : ''}
        </div>
        <div class="erp-scenario">${it.scenario_name}</div>
      </div>`;
    }).join('');
    const lockHtml = data.locked_count > 0
      ? `<div class="erp-lock">🔒 Daha fazlası için ELITE</div>`
      : '';
    body.innerHTML = `<div class="erp-wrap">${itemsHtml}${lockHtml}</div>`;
  } catch (err) {
    // sessiz başarısızlık — JSON yoksa veya hatalıysa panel gizli kalır
  }
}


// ── Meta ──────────────────────────────────────────────────────────────────────
function renderUpdateTime(meta) {
  if (!meta) return;
  const el = document.getElementById("update-time");
  if (!el) return;
  if (meta.canli_guncelleme) {
    el.innerHTML = '🟢 Canlı · ' + meta.canli_guncelleme + ' güncellendi';
  } else {
    // meta.guncelleme UTC olarak gelir — İstanbul (+3) için 3 saat ekle
    var saat = meta.guncelleme || '';
    if (saat && saat.indexOf(':') !== -1) {
      var parts = saat.split(':');
      var h = (parseInt(parts[0], 10) + 3) % 24;
      saat = (h < 10 ? '0' : '') + h + ':' + parts[1];
    }
    el.innerHTML = (meta.tarih || '') + ' · ' + saat;
  }
}


// genHistory artık kullanılmıyor — gerçek veri JSON'dan geliyor


// ── XU100 akış grafikleri — app.py render_synthetic_sentiment_panel İKİZİ (1 Eki 2026) ─
// Veri: xu100_grafik [{date, mf, stp, price, sentiment?}] — backend bunu app'in
// kendi hesabından (sentiment_chart_core.calculate_sentiment_chart) üretir.
// Çizim kuralları app.py Altair panelinden birebir alındı:
//   sol: bar = MF_Smooth (endeks ölçeği: max(20, ⌈|max|·1.15 / 5⌉·5), simetrik),
//        fiyat çizgisi bağımsız eksen (zero=False, sağda)
//   sağ: STP (sarı, 3px) + Fiyat (açık mavi, 2px) aynı eksen (min·0.999 / max·1.001),
//        arası gri %15 dolgu; Sentiment 0-10 kesik beyaz çizgi (sol eksen 0,2..10)
// sentiment alanı yoksa (backend eski formüle düştüyse) sentiment çizgisi çizilmez.
const _XC = { W: 520, H: 290, l: 40, r: 52, t: 12, b: 58 };

function _niceTicks(lo, hi, k = 5) {
  const raw = (hi - lo) / k;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const st = [1, 2, 2.5, 5, 10].map(m => m * mag).reduce((a, b) => Math.abs(b - raw) < Math.abs(a - raw) ? b : a);
  const out = [];
  for (let v = Math.ceil(lo / st) * st; v <= hi + 1e-9; v += st) out.push(v);
  return { lo: Math.floor(lo / st) * st, hi: Math.ceil(hi / st) * st, ticks: out };
}
function _fmtAx(v) {
  return v >= 1000 ? v.toLocaleString("tr-TR", { maximumFractionDigits: 0 }) : v.toFixed(2);
}
function _xFrame(grafik) {
  const { W, H, l, r, t, b } = _XC;
  const pw = W - l - r, ph = H - t - b, n = grafik.length, step = pw / n;
  const X = i => l + step * (i + 0.5);
  const xl = grafik.map((g, i) => {
    const x = X(i).toFixed(1), y = t + ph + 10;
    return `<text x="${x}" y="${y}" font-size="9" fill="#94a3b8" text-anchor="end" transform="rotate(-45 ${x} ${y})">${g.date}</text>`;
  }).join("");
  return { W, H, l, r, t, b, pw, ph, n, step, X, xl };
}

function makeMomentumSVG(grafik, isIndex = true) {
  const f = _xFrame(grafik);
  const mf = grafik.map(g => g.mf), pr = grafik.map(g => g.price);
  const mx = Math.max(...mf.map(Math.abs));
  // app.py bar ekseni: endeks → ±max(20, ⌈|max|·1.15/5⌉·5) · hisse → ±max(30, |max|·1.05)
  const lim = isIndex ? Math.max(20, Math.ceil((mx * 1.15) / 5) * 5) : Math.max(30, mx * 1.05);
  const Ym = v => f.t + f.ph / 2 - (v / lim) * (f.ph / 2);
  const P = _niceTicks(Math.min(...pr), Math.max(...pr));
  const Yp = v => f.t + f.ph - ((v - P.lo) / (P.hi - P.lo)) * f.ph;
  const stepM = lim <= 40 ? 10 : lim <= 80 ? 20 : 50;
  let grid = "";
  for (let v = Math.ceil(-lim / stepM) * stepM; v <= lim + 1e-9; v += stepM) {
    const y = Ym(v).toFixed(1);
    grid += `<line x1="${f.l}" x2="${f.l + f.pw}" y1="${y}" y2="${y}" stroke="#1e293b" stroke-width="1"/>` +
            `<text x="${f.l - 6}" y="${(+y + 3).toFixed(1)}" font-size="10" fill="#94a3b8" text-anchor="end">${v.toFixed(0)}</text>`;
  }
  const pl = P.ticks.map(v => `<text x="${f.l + f.pw + 6}" y="${(Yp(v) + 3).toFixed(1)}" font-size="10" fill="#94a3b8">${_fmtAx(v)}</text>`).join("");
  const bw = Math.min(12, f.step * 0.8);
  const bars = mf.map((v, i) => {
    const y0 = Ym(0), y1 = Ym(v);
    return `<rect x="${(f.X(i) - bw / 2).toFixed(1)}" y="${Math.min(y0, y1).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.abs(y1 - y0).toFixed(1)}" fill="${v > 0 ? "#5B84C4" : "#ef4444"}" opacity="0.9"/>`;
  }).join("");
  const line = pr.map((v, i) => `${f.X(i).toFixed(1)},${Yp(v).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${f.W} ${f.H}" width="100%" style="display:block" role="img" aria-label="Akıllı Para İvmesi ve Fiyat">
${grid}${pl}${bars}<polyline fill="none" stroke="#bfdbfe" stroke-width="2" points="${line}"/>${f.xl}</svg>`;
}

function makeSentimentSVG(grafik) {
  const f = _xFrame(grafik);
  const pr = grafik.map(g => g.price), st = grafik.map(g => g.stp);
  const hasSent = grafik.every(g => typeof g.sentiment === "number");
  const lo = Math.min(...st, ...pr) * 0.999, hi = Math.max(...st, ...pr) * 1.001;
  const Ys = v => f.t + f.ph - ((v - lo) / (hi - lo)) * f.ph;
  const Yse = v => f.t + f.ph - (v / 10) * f.ph;
  let grid = "";
  if (hasSent) {
    for (let v = 0; v <= 10; v += 2) {
      const y = Yse(v).toFixed(1);
      grid += `<line x1="${f.l}" x2="${f.l + f.pw}" y1="${y}" y2="${y}" stroke="#1e293b" stroke-width="1"/>` +
              `<text x="${f.l - 6}" y="${(+y + 3).toFixed(1)}" font-size="10" fill="#94a3b8" text-anchor="end">${v}</text>`;
    }
  }
  const pl = _niceTicks(lo, hi).ticks.filter(v => v >= lo && v <= hi)
    .map(v => `<text x="${f.l + f.pw + 6}" y="${(Ys(v) + 3).toFixed(1)}" font-size="10" fill="#94a3b8">${_fmtAx(v)}</text>`).join("");
  const ps = st.map((v, i) => `${f.X(i).toFixed(1)},${Ys(v).toFixed(1)}`);
  const pp = pr.map((v, i) => `${f.X(i).toFixed(1)},${Ys(v).toFixed(1)}`);
  const sent = hasSent
    ? `<polyline fill="none" stroke="#f8fafc" stroke-opacity="0.88" stroke-width="1.2" stroke-dasharray="4 3" stroke-linecap="round" points="${grafik.map((g, i) => `${f.X(i).toFixed(1)},${Yse(g.sentiment).toFixed(1)}`).join(" ")}"/>`
    : "";
  return `<svg viewBox="0 0 ${f.W} ${f.H}" width="100%" style="display:block" role="img" aria-label="Sentiment ve Fiyat">
${grid}${pl}<polygon fill="gray" opacity="0.15" points="${ps.concat(pp.slice().reverse()).join(" ")}"/>
<polyline fill="none" stroke="#fbbf24" stroke-width="3" points="${ps.join(" ")}"/>
<polyline fill="none" stroke="#bfdbfe" stroke-width="2" points="${pp.join(" ")}"/>${sent}${f.xl}</svg>`;
}


// ── Counter Bar → ELITE info strip ───────────────────────────────────────────
function renderCounterBar(d) {
  const el = document.getElementById("counter-bar");
  if (!el) return;
  el.innerHTML = `
    <div class="counter-elite-badge">ELITE</div>
    <div class="counter-elite-text">
      Gün içi anlık sinyaller, sentiment ve momentum taramaları ve haftalık tarama raporu
      yalnızca <strong style="color:#70a8ff">Telegram ELITE</strong> kanalındadır — bu site kapanış verisinin özet radarıdır.
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" style="color:#70a8ff;text-decoration:underline;margin-left:6px">Kanala Katıl →</a>
    </div>
  `;
}


// ── XU100 Panel (istatistikler + 2 grafik) ────────────────────────────────────
function renderXU100Panel(d, ozet, grafik, opt = {}) {
  const label   = opt.label || "XU100";
  const isIndex = opt.isIndex !== false;
  if (!d || d.hata) {
    document.getElementById("xu100-body").innerHTML = errHTML(`${label} verisi alınamadı`);
    return;
  }

  const pos     = d.degisim_pct >= 0;
  const chgCls  = pos ? "pos" : "neg";
  const chgSign = pos ? "▲" : "▼";
  const dotLeft = Math.max(2, Math.min(96, d.pozisyon_pct ?? 50));
  const skor    = ozet?.genel_skor ?? 0;

  const htag = document.getElementById("xu100-header-price");
  if (htag) {
    htag.textContent = fmt(d.kapanis);
    htag.classList.toggle('price-pos', pos);
    htag.classList.toggle('price-neg', !pos);
  }

  // Grafik HTML — gerçek veri varsa kullan, yoksa boş mesaj
  const chartHtml = grafik && grafik.length >= 5
    ? `<div class="chart-grid">
        <div class="chart-pane">
          <div class="chart-pane-label" style="color:#38bdf8;font-weight:700">Akıllı Para İvmesi &amp; Fiyat — ${label}</div>
          ${makeMomentumSVG(grafik, isIndex)}
        </div>
        <div class="chart-pane">
          <div class="chart-pane-label" style="color:#38bdf8;font-weight:700">${grafik.every(g => typeof g.sentiment === "number") ? `Sentiment &amp; Fiyat — ${label}` : `Fiyat (mavi) ↔ Eğilim (sarı) — ${label}`}</div>
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
    ${opt.label ? `<div style="font-size:11px;color:var(--text-muted);margin:-4px 0 8px;display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <span>${opt.fiyatEtiket || `Fiyat saati: <strong style="color:var(--text-dim)">${opt.fiyatSaati || "-"}</strong>`}</span>
      <a href="#" onclick="xu100eDon();return false;" style="color:#38bdf8">← XU100'e dön</a></div>` : ""}
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
      ${opt.hideSkor ? "" : `<div class="stat-chip">
        <span class="stat-chip-label">Skor</span>
        <span class="stat-chip-val ${skor >= 65 ? 'g' : skor >= 40 ? 'o' : 'r'}">${skor.toFixed(0)}</span>
      </div>`}
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
function renderTeknikSeviyeler(d, label = "XU100") {
  if (!d || d.hata) {
    document.getElementById("levels-body").innerHTML = errHTML("Seviye verisi alınamadı");
    return;
  }

  const tag = document.getElementById("levels-xu-tag");
  if (tag) tag.textContent = `${label} — ${fmt(d.kapanis)}`;

  const k = d.kapanis;

  // Gerçek EMA/SMA değerleri — latest.json'dan okunur (scan_core.py hesaplar)
  const ema5   = d.ema5   || k;
  const ema8   = d.ema8   || k;
  const ema13  = d.ema13  || k;
  const sma50  = d.sma50;
  const sma100 = d.sma100 || (d.sma50 ? (d.sma50 + d.sma200) / 2 : k);
  const sma200 = d.sma200;
  const ema144 = d.ema144 || (d.sma200 ? d.sma200 : k);

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
          <th class="lv-current-th">${label}</th>
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
  const _hb = document.getElementById("hacim-baslik");
  if (_hb) _hb.textContent = "💧 Smart Money Hacim Analizi";

  const poc = document.getElementById("poc-tag");
  const s200 = ozet.sma200_ustu_pct ?? 0;
  if (poc) poc.textContent = `%${s200.toFixed(0)} Hisse SMA200 Üstü`;

  const sc = s200 >= 60 ? "var(--green)" : s200 >= 40 ? "var(--orange)" : "var(--red)";


  document.getElementById("hacim-body").innerHTML = `
    <div class="ict-grid tek-kutu-grid">
      <div class="ict-cell" style="border-color:var(--orange)">
        <div class="ict-cell-title" style="color:var(--orange)">📊 Piyasa Akışı</div>
        <div class="ict-cell-val" style="color:${sc}">%${s200.toFixed(0)}</div>
        <div class="ict-cell-sub">SMA200 Üstü Hisse · ${150} tarandı</div>
      </div>
      ${kilitliTekKutu(["💧 POC Bölgesi", "⚡ RVOL Oranı", "🔥 Kümülatif Delta", "📈 OBV Analizi",
                        "🎯 Hacim Anomalisi", "💼 VSA Sinyali", "📊 Akıllı Para Skoru"])}
    </div>
  `;
}


// ── KİLİTLİ BAŞLIKLAR TEK KUTU (2 Eki 2026) ───────────────────────────────────
// Kullanıcı: mobilde 7-8 ayrı kilitli kutucuk dağınık ve çok yer kaplıyor → panelde
// gerçek bilgi kutusu solda kalır, kilitli başlıkların HEPSİ tek ELITE kutusunda listelenir.
function kilitliTekKutu(basliklar) {
  return `
    <div class="ict-cell tek-kilit-kutu" style="cursor:pointer"
         onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <span class="ict-cell-title" style="margin:0">🔒 ELITE aboneler görür</span>
        <span class="elite-badge">ELITE</span>
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:8px">
        ${basliklar.map(b => `<span style="font-size:10.5px;line-height:1.3;padding:3px 8px;border-radius:12px;border:1px solid rgba(139,92,246,0.35);background:rgba(139,92,246,0.08);color:var(--text-dim)">${b}</span>`).join("")}
      </div>
    </div>`;
}

// ── AKILLI PARA · HİSSE kartı (2 Eki 2026) ───────────────────────────────────
// Hisse seçilince Hacim paneli o hissenin akıllı para kartına döner. Açık: endekse göre
// güç (20 gün). ELITE kutusu: CMF 5/20, hacim/ortalama, hacimde endekse göre (OBV),
// alım-satım hacim dengesi — admin değerleri görür, diğerleri sadece başlıkları.
function renderHisseAkilliPara(j) {
  const body = document.getElementById("hacim-body");
  const a = j && j.akilli;
  const t = j?.hisse?.ticker || "";
  if (!body || !a) return false;
  const bas = document.getElementById("hacim-baslik");
  if (bas) bas.textContent = `💧 Akıllı Para · ${t}`;
  const tag = document.getElementById("poc-tag");
  if (tag) tag.textContent = "son 20 gün";
  const sayi = (v, n = 1) => v == null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(n).replace(".", ",")}`;
  const g = a.guc20 ?? 0, gr = g >= 0 ? "var(--green)" : "var(--red)";
  const cmfEt = v => v == null ? "—" : v > 0.1 ? "alıcı ağır" : v < -0.1 ? "satıcı ağır" : "dengeye yakın";
  const hvEt = v => v == null ? "—" : v >= 1.5 ? "ortalamanın belirgin üstünde" : v >= 1.1 ? "ortalamanın üstünde" : v >= 0.9 ? "ortalama civarı" : "ortalamanın altında";
  const OBV = { outperform_strong: "endeksten belirgin güçlü", outperform_mild: "endeksten biraz güçlü", inline: "endeksle benzer",
                underperform_mild: "endeksten biraz zayıf", underperform_strong: "endeksten belirgin zayıf" };
  const UD = { strong_buyer: "alıcı net hakim", buyer: "alıcı ağır", balanced: "denge", seller: "satıcı ağır", strong_seller: "satıcı net hakim" };
  const satir = [
    ["5 gün akıllı para (CMF)", `${sayi(a.cmf5, 2)} · ${cmfEt(a.cmf5)}`],
    ["20 gün akıllı para (CMF)", `${sayi(a.cmf20, 2)} · ${cmfEt(a.cmf20)}`],
    ["Hacim / ortalama", a.hacim_ort == null ? "—" : `${a.hacim_ort.toFixed(2).replace(".", ",")}× · ${hvEt(a.hacim_ort)}`],
    ["Hacimde endekse göre (OBV)", OBV[a.obv_endeks] || "—"],
    ["Alım-satım hacim dengesi", a.ud_oran == null ? "—" : `${a.ud_oran.toFixed(2).replace(".", ",")} · ${UD[a.ud_durum] || "—"}`],
  ];
  const elite = window._smrAdmin
    ? `<div style="display:flex;justify-content:space-between;align-items:center"><span class="ict-cell-title" style="margin:0">🔓 ELITE · admin görünümü</span><span class="elite-badge">ELITE</span></div>
       ${satir.map(([k, v]) => `<div style="display:flex;justify-content:space-between;gap:8px;font-size:11.5px;line-height:1.9;border-bottom:1px solid rgba(139,92,246,.15)"><span style="color:var(--text-dim)">${k}</span><strong style="color:var(--text);text-align:right">${v}</strong></div>`).join("")}`
    : null;
  body.innerHTML = `
    <div class="ict-grid tek-kutu-grid">
      <div class="ict-cell" style="border-color:${gr}">
        <div class="ict-cell-title" style="color:${gr}">📊 Endekse göre güç</div>
        <div class="ict-cell-val" style="color:${gr}">${sayi(g)} puan</div>
        <div class="ict-cell-sub">${t} %${sayi(a.hisse20)} · XU100 %${sayi(a.xu20)}<br>
          Son 20 günde endeksten ${Math.abs(g).toFixed(1).replace(".", ",")} puan ${g >= 0 ? "iyi" : "kötü"}</div>
      </div>
      ${elite ? `<div class="ict-cell">${elite}</div>` : kilitliTekKutu(satir.map(x => x[0]))}
    </div>`;
  return true;
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


  document.getElementById("composite-body").innerHTML = `
    <div class="ict-grid tek-kutu-grid">
      <div class="ict-cell" style="border-color:var(--cyan)">
        <div class="ict-cell-title" style="color:var(--cyan)">🗺️ Composite Skor</div>
        <div class="ict-cell-val" style="color:${sc}">${skor.toFixed(0)}/100</div>
        <div class="ict-cell-sub">SMA200+: %${s200pct.toFixed(0)} · ${lbl}</div>
      </div>
      ${kilitliTekKutu(["📐 Vade Uyumu", "📊 RSI Momentum", "🔥 Güçlü Sinyal", "📈 Trend Skoru",
                        "⚖️ Risk / Ödül", "💧 SMA50 Üstü", "🎯 Piyasa Fazı"])}
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
  if (tag) tag.innerHTML = `<span style="color:${sColor}">Piyasa skoru ${skor.toFixed(0)} / 100 · ${sLabel}</span>`;

  const k = d.kapanis;

  document.getElementById("ict-body").innerHTML = `
    <div class="ict-grid tek-kutu-grid">
      <div class="ict-cell" style="border-color:var(--cyan)">
        <div class="ict-cell-title" style="color:var(--cyan)">📍 Mevcut Fiyat</div>
        <div class="ict-cell-val" style="color:var(--text)">${fmt(k)}</div>
        <div class="ict-cell-sub" style="color:${d.degisim_pct>=0?'var(--green)':'var(--red)'}">
          ${d.degisim_pct>=0?'▲':'▼'} ${Math.abs(d.degisim_pct).toFixed(2)}%
        </div>
      </div>
      ${kilitliTekKutu(["📈 Yükseliş Trendi (Bullish Box)", "⚡ Enerji Durumu", "🗺️ Fiyat Haritası",
                        "📊 Alıcılar Bakışı — OB Detayı", "🎯 Yakın Hedef"])}
    </div>
  `;
}


// ── Sol Sidebar ───────────────────────────────────────────────────────────────
function renderSidebarLeft(d, ozet, opt = {}) {
  if (!d || d.hata) return;
  // 2 Eki 2026 — seçili hisse: başlıkta hisse adı; SKOR (piyasa geneli) yerine hissenin 52H konumu
  const _ot = document.getElementById("ozet-ticker");
  if (_ot) _ot.textContent = opt.label || "XU100";
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
        ${opt.label ? `52H <strong style="color:var(--cyan)">%${Math.round(d.pozisyon_pct ?? 0)}</strong>` : `SKOR <strong style="color:var(--cyan)">${longSkor}/100</strong>`}
      </span>
      <span style="font-size:10px;background:var(--bg4);border:1px solid var(--border2);border-radius:3px;padding:2px 7px;color:var(--text-dim)">
        SMA200 <strong style="color:var(--red)">${fmt(d.sma200)}</strong>
      </span>
    </div>
    <div style="font-size:10px;color:${metColor};font-weight:700;margin-bottom:4px">${met}/4</div>
    <div style="font-size:10.5px;color:var(--text-dim);line-height:1.6">
      Gün ${ok(hacimOk)} · SMA50 ${ok(obvOk)} · SMA200 ${ok(yapiOk)} · RSI ${ok(rsiOk)}
    </div>
    <!-- GENEL ÖZET teaser -->
    <div style="margin-top:8px;border:1px dashed var(--border2);border-radius:4px;padding:6px 8px;background:rgba(10,13,26,0.6)">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none;font-size:10px;color:var(--text-dim);line-height:1.5">
        HH+HL Yapısı · Kümülatif Delta · SFP · LONG Radar · Stop seviyesi
      </div>
      <div style="text-align:center;margin-top:5px;cursor:pointer" class="smr-lock" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';">
        <span style="font-size:9px;color:#70a8ff;font-weight:700">+ daha fazlası için ELITE →</span>
      </div>
    </div>
  `;

  renderGauge("sidebar-gauge", ozet?.genel_skor ?? 0);

  // KURUMSAL İLGİ mini (XU100 Özet yerine)
  document.getElementById("sidebar-xu100mini").innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px">
      <span style="font-size:13px;font-weight:800;color:var(--text-dim)">🔒 ••/100</span>
      <span class="elite-badge" style="font-size:9px;padding:1px 6px">ELITE</span>
    </div>
    <div style="position:relative;overflow:hidden;margin-top:5px;max-height:38px">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Trend</span><span class="sidebar-stat-val g">••••</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Momentum</span><span class="sidebar-stat-val c">••••</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">Hacim Kalitesi</span><span class="sidebar-stat-val g">••••</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">RS Gücü</span><span class="sidebar-stat-val g">••••</span></div>
        <div class="sidebar-stat-row"><span class="sidebar-stat-label">SM İzi</span><span class="sidebar-stat-val" style="color:#70a8ff">••••</span></div>
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
      <div style="position:relative;overflow:hidden;margin-top:6px;max-height:38px">
        <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
          <div class="signal-row"><div class="signal-dot g"></div><span>ATR Risk Bölgesi: ••••</span></div>
          <div class="signal-row"><div class="signal-dot c"></div><span>Bollinger: ••••</span></div>
          <div class="signal-row"><div class="signal-dot o"></div><span>ADX Güç: ••••</span></div>
          <div class="signal-row"><div class="signal-dot g"></div><span>Stoch RSI: ••••</span></div>
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
  if (ve) ve.textContent = `HACİM & VSA: 🔒 ELITE`;
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
        <span style="font-size:11px;font-weight:700;color:var(--text-dim);margin-left:4px">${KILIT_MASK}</span>
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
            Trend + Momentum + Hacim üçlü uyumu
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
            ICT + SMC + Kurumsal iz üçlü kesişimi
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

// Piyasa değeri sıralaması (sabit, yılda 1 güncellenir)
const BIST_MCAP_RANK = {
  THYAO:1,  GARAN:2,  AKBNK:3,  ISCTR:4,  SAHOL:5,  KCHOL:6,  EREGL:7,
  TCELL:8,  SISE:9,   TUPRS:10, BIMAS:11, FROTO:12, TOASO:13, HALKB:14,
  VAKBN:15, YKBNK:16, ENKAI:17, TKFEN:18, PGSUS:19, ASELS:20, PETKM:21,
  CCOLA:22, EKGYO:23, ARCLK:24, GUBRF:25, KRDMD:26, SASA:27,  TTKOM:28,
  TTRAK:29, KOZAL:30, SKBNK:31, QNBFB:32, ALBRK:33, AGHOL:34, AEFES:35,
  BRISA:36, DOAS:37,  LOGO:38,  MPARK:39, OTKAR:40
};

function _karmaSkoru(h) {
  const rsiN  = Math.max(0, Math.min(100, (h.rsi - 50) / 22 * 100));
  const hN    = Math.min(100, ((h.hacim_x - 1) / 4) * 100);
  const dN    = Math.max(0, Math.min(100, (h.degisim_pct + 5) / 15 * 100));
  return rsiN * 0.4 + hN * 0.4 + dN * 0.2;
}

function renderOneCikanlar(ozet) {
  const el = document.getElementById("one-cikanlar-panel");
  if (!el) return;

  const liste = [...(ozet?.one_cikanlar || [])];
  if (!liste.length) { el.innerHTML = ''; return; }

  // Karma skor hesapla + sırala (eşit puanlılarda piyasa değerine göre)
  liste.forEach(h => { h._karma = _karmaSkoru(h); });
  liste.sort((a, b) => {
    const diff = b._karma - a._karma;
    if (Math.abs(diff) > 2) return diff;
    return (BIST_MCAP_RANK[a.ticker] || 999) - (BIST_MCAP_RANK[b.ticker] || 999);
  });

  const top5 = liste.slice(0, 5);
  window._ocStocks = top5;
  const kalan = liste.length - 5;

  const card = (h) => {
    const pos   = h.degisim_pct >= 0;
    const dSign = pos ? '+' : '';
    const rsiClr = h.rsi >= 70 ? '#f97316' : h.rsi >= 55 ? '#22c55e' : '#94a3b8';
    const topClr = pos ? '#22c55e' : '#ef4444';
    const hasDetail = h.chart_data && h.chart_data.length > 0;
    return `
      <div class="oc-card" id="oc-card-${h.ticker}" style="border-top-color:${topClr};cursor:pointer" onclick="openHisseDetay('${h.ticker}')">
        <div class="oc-ticker">${h.ticker}</div>
        <div class="oc-price">${h.close?.toLocaleString('tr-TR',{minimumFractionDigits:2})}</div>
        <div class="oc-change ${pos ? 'pos' : 'neg'}">${dSign}${h.degisim_pct}%</div>
        <div class="oc-meta">
          <span class="oc-badge" style="color:${rsiClr}">RSI ${h.rsi}</span>
          <span class="oc-badge">⚡ ${h.hacim_x}×</span>
        </div>
        ${hasDetail ? '<div style="font-size:8px;color:#64748b;margin-top:4px;text-align:center">▼ detay</div>' : ''}
      </div>`;
  };

  el.innerHTML = `
    <div class="panel-header green">
      <div class="panel-title">📊 Günün Öne Çıkanları</div>
      <div class="panel-tag" style="color:#94a3b8">Her gün 18:45 güncellenir</div>
    </div>
    <div class="panel-body">
      <div class="oc-strip">
        ${top5.map(card).join('')}
        <div class="oc-card oc-card-locked" onclick="openPlansModal()">
          <div class="elite-badge" style="font-size:9px;padding:2px 7px">ELITE</div>
          <div style="font-size:10px;color:#94a3b8;margin-top:6px">+${kalan}</div>
          <div style="font-size:9px;color:#64748b">hisse daha</div>
        </div>
      </div>
      <div style="font-size:10px;color:#475569;margin-top:6px">
        Sıralama: RSI %40 · Hacim %40 · Momentum %20 · Eşitte piyasa değeri
      </div>
    </div>`;
}


// ── Hisse Detay (Öne Çıkanlar tıklama) ───────────────────────────────────────
function openHisseDetay(ticker) {
  const h = (window._ocStocks || []).find(s => s.ticker === ticker);
  if (!h) return;
  const panel = document.getElementById('hisse-detay-panel');
  if (!panel) return;
  document.querySelectorAll('.oc-card').forEach(c => c.style.outline = '');
  const aktifKart = document.getElementById('oc-card-' + ticker);
  if (aktifKart) aktifKart.style.outline = '2px solid #22c55e';
  panel.style.display = 'block';
  renderHisseDetayPanel(h);
  panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeHisseDetay() {
  const panel = document.getElementById('hisse-detay-panel');
  if (panel) panel.style.display = 'none';
  document.querySelectorAll('.oc-card').forEach(c => c.style.outline = '');
}

function renderHisseDetayPanel(h) {
  const panel = document.getElementById('hisse-detay-panel');
  if (!panel) return;

  const pos     = h.degisim_pct >= 0;
  const dSign   = pos ? '+' : '';
  const clr     = pos ? '#22c55e' : '#ef4444';
  const rc      = rsiClass(h.rsi);
  const rejim   = h.rejim || '—';
  const rRenk   = h.rejim_renk === 'green' ? '#22c55e' : h.rejim_renk === 'red' ? '#ef4444' : '#f97316';

  // 52H pozisyon bar
  const poz    = h.pozisyon_pct ?? 50;
  const pozBar = `
    <div style="position:relative;height:6px;background:#1e293b;border-radius:3px;margin:4px 0">
      <div style="position:absolute;left:0;top:0;height:6px;width:${poz}%;background:${clr};border-radius:3px"></div>
    </div>
    <div style="display:flex;justify-content:space-between;font-size:9px;color:#64748b">
      <span>${fmt(h.yillik_dusuk)}</span><span style="color:#94a3b8">${poz}%</span><span>${fmt(h.yillik_yuksek)}</span>
    </div>`;

  // EMA seviyeleri
  const cl  = (v) => v && h.close ? (h.close > v ? 'g' : 'r') : 'c';
  const dot = (v) => v && h.close ? (h.close > v ? '▲' : '▼') : '·';
  const levelRow = (label, v) => v ? `
    <tr>
      <td style="color:#64748b;font-size:10px;padding:3px 6px">${label}</td>
      <td class="${cl(v)}" style="font-size:10px;padding:3px 6px;text-align:right">${dot(v)} ${fmt(v)}</td>
    </tr>` : '';

  // OBV + RSI trend
  const obvClr   = h.obv_yonu  === 'yukari' ? '#22c55e' : '#ef4444';
  const obvTxt   = h.obv_yonu  === 'yukari' ? '↑ Alış Baskısı' : '↓ Satış Baskısı';
  const rsiTrClr = h.rsi_trend === 'yukari' ? '#22c55e' : '#ef4444';
  const rsiTrTxt = h.rsi_trend === 'yukari' ? '↑ Güçleniyor' : '↓ Zayıflıyor';

  // Grafikler
  const grafik = h.chart_data || [];
  const hasSVG = grafik.length > 0;
  const svgBok = hasSVG
    ? `<div style="margin-top:10px">${makeMomentumSVG(grafik, false)}</div>
       <div style="margin-top:6px">${makeSentimentSVG(grafik)}</div>`
    : `<div style="color:#475569;font-size:11px;margin-top:10px;text-align:center;padding:12px 0">
         Grafik verisi bir sonraki güncellemede hazır olacak (18:45)
       </div>`;

  panel.innerHTML = `
    <div class="panel-header" style="background:linear-gradient(135deg,#0f172a,#1e293b);border-left:3px solid ${clr}">
      <div style="display:flex;align-items:center;justify-content:space-between">
        <div>
          <span style="font-size:15px;font-weight:700;color:#e2e8f0">${h.ticker}</span>
          <span style="font-size:13px;color:${clr};margin-left:8px;font-weight:600">${dSign}${h.degisim_pct}%</span>
          <span style="font-size:12px;color:#94a3b8;margin-left:6px">${fmt(h.close)} ₺</span>
        </div>
        <button onclick="closeHisseDetay()" style="background:none;border:none;color:#64748b;cursor:pointer;font-size:16px;padding:4px 8px">✕</button>
      </div>
      <div style="margin-top:4px">
        <span style="font-size:10px;font-weight:600;color:${rRenk};background:${rRenk}20;padding:2px 8px;border-radius:10px">${rejim}</span>
      </div>
    </div>
    <div class="panel-body">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:10px">
        <div class="stat-chip">
          <span class="stat-chip-label">SMA 50</span>
          <span class="stat-chip-val ${cl(h.sma50)}">${fmt(h.sma50)}</span>
        </div>
        <div class="stat-chip">
          <span class="stat-chip-label">SMA 200</span>
          <span class="stat-chip-val ${cl(h.sma200)}">${fmt(h.sma200)}</span>
        </div>
        <div class="stat-chip">
          <span class="stat-chip-label">RSI</span>
          <span class="stat-chip-val ${rc}">${h.rsi}</span>
        </div>
        <div class="stat-chip">
          <span class="stat-chip-label">Hacim</span>
          <span class="stat-chip-val" style="color:#f97316">⚡ ${h.hacim_x}×</span>
        </div>
      </div>
      <div style="margin-bottom:10px">
        <div style="font-size:10px;color:#64748b;margin-bottom:2px">52 Haftalık Konum</div>
        ${pozBar}
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px">
        <div style="background:#0f172a;border-radius:6px;padding:8px">
          <div style="font-size:9px;color:#64748b;margin-bottom:3px">OBV Trendi</div>
          <div style="font-size:11px;font-weight:600;color:${obvClr}">${obvTxt}</div>
        </div>
        <div style="background:#0f172a;border-radius:6px;padding:8px">
          <div style="font-size:9px;color:#64748b;margin-bottom:3px">RSI Trendi</div>
          <div style="font-size:11px;font-weight:600;color:${rsiTrClr}">${rsiTrTxt}</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:10px">
        <div style="background:#0f172a;border-radius:6px;padding:8px">
          <div style="font-size:9px;color:#64748b;margin-bottom:3px">Destek (20g)</div>
          <div style="font-size:12px;font-weight:600;color:#22c55e">${fmt(h.destek)}</div>
        </div>
        <div style="background:#0f172a;border-radius:6px;padding:8px">
          <div style="font-size:9px;color:#64748b;margin-bottom:3px">Direnç (20g)</div>
          <div style="font-size:12px;font-weight:600;color:#ef4444">${fmt(h.direnc)}</div>
        </div>
      </div>
      <div style="background:#0f172a;border-radius:6px;padding:8px;margin-bottom:6px">
        <div style="font-size:9px;color:#64748b;margin-bottom:6px">EMA Seviyeleri</div>
        <table style="width:100%;border-collapse:collapse">
          ${levelRow('EMA 5', h.ema5)}
          ${levelRow('EMA 8', h.ema8)}
          ${levelRow('EMA 13', h.ema13)}
        </table>
      </div>
      ${svgBok}
    </div>`;
}

// ── Kurumsal İlgi Paneli ──────────────────────────────────────────────────────
function renderKurumsalPanel(xu100) {
  const el = document.getElementById("kurumsal-panel");
  if (!el) return;


  const cell = (title, val, sub) => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${KILIT_MASK}</div>
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
      <div class="panel-tag" style="color:var(--orange)">ELITE</div>
    </div>
    <div class="panel-body">
      <div class="ict-grid">
        <div class="ict-cell" style="border-color:var(--orange)">
          <div class="ict-cell-title" style="color:var(--orange)">💼 1. YAPI — Temel</div>
          <div class="ict-cell-val">${KILIT_MASK}</div>
          <div class="ict-cell-sub">Kurumsal ilgi skoru</div>
        </div>
        ${cell('📈 Trend Kalitesi','<span style="color:var(--green)">A+</span>','Trend kalitesi')}
        ${cell('⚡ Momentum Gücü','<span style="color:var(--cyan)">YÜKSEK</span>','MACD + RSI uyumu')}
        ${cell('💧 Hacim Kalitesi','<span style="color:var(--green)">RVOL 1.42×</span>','Birikim mi dağıtım mı')}
        ${cell('🔥 RS Gücü','<span style="color:var(--green)">+18.4%</span>','Endekse göre güç')}
        ${cell('🎯 Smart Money İzi','<span style="color:#70a8ff">5/7 İz</span>','Kurumsal ayak izi')}
        ${cell('📊 Delta Birikimi','<span style="color:var(--green)">+284M</span>','5 günlük kümülatif')}
        ${cell('🛡️ OBV Yönü','<span style="color:var(--green)">YUKARI ↑</span>','Birikim mi dağıtım mı')}
      </div>
    </div>
  `;
}


// ── Teknik Yol Haritası Genişletilmiş ────────────────────────────────────────
function renderTeknikYolPanel(ozet) {
  const el = document.getElementById("teknik-yol-panel");
  if (!el) return;

  const skor = ozet?.genel_skor ?? 0;
  const sc   = skor >= 65 ? "var(--green)" : skor >= 40 ? "var(--orange)" : "var(--red)";
  const lbl  = skor >= 65 ? "YÜKSELİŞ" : skor >= 40 ? "NÖTR" : "DÜŞÜŞ";

  const cell = (title, val, sub, plan='elite') => `
    <div class="ict-cell">
      <div class="ict-cell-title">${title}</div>
      <div class="ict-cell-blur">
        <div class="ict-cell-val">${KILIT_MASK}</div>
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
        ${cell('🔷 Fiyat — Formasyon','<span style="color:#70a8ff">BULL FLAG</span>','Formasyon tespiti')}
        ${cell('📊 Trend Skoru','<span style="color:var(--green)">84/100</span>','Algo trend kalitesi')}
        ${cell('💧 Hacim Algoritması','<span style="color:var(--cyan)">BIRIKIM</span>','Smart Money hacim modeli')}
        ${cell('📋 Teknik Özet','<span style="color:var(--green)">9 ALIM</span>','12 indikatör sonucu')}
        ${cell('🎯 Trade Planı — Giriş','<span style="color:var(--green)">14,820–14,960</span>','Optimal giriş bölgesi')}
        ${cell('⚖️ Risk / Ödül','<span style="color:var(--cyan)">1:2.4</span>','Stop + 2 hedef seviye')}
        ${cell('🌀 Bollinger Konumu','<span style="color:var(--orange)">Üst banda yakın</span>','Bant içindeki konum')}
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
        <div class="ict-cell-val">${KILIT_MASK}</div>
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
          <div class="ict-cell-val">${KILIT_MASK}</div>
          <div class="ict-cell-sub">R1 + R2 toplamı bugün</div>
        </div>
        ${cell('📡 Radar 1 — Momentum','<span style="color:var(--green)">7/7 Hisse</span>','Hisse listesi')}
        ${cell('🎯 Radar 2 — Trend Lider','<span style="color:var(--cyan)">5/5 Hisse</span>','Hisse listesi')}
        ${cell('🔥 Ortak Set-Up','<span style="color:var(--orange)">5 Hisse</span>','R1+R2 kesişim')}
        ${cell('🏆 TOP 20 Master','<span style="color:#70a8ff">20 Hisse</span>','Algo sıralama listesi')}
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
      <span>SMA50: ${stpOk?'Üstünde':'Altında'}</span>
    </div>
    <div class="signal-row">
      ${dot(r1Ok)}
      <span>RSI (14): ${d?.rsi?.toFixed(0)||'—'}</span>
    </div>
    <div class="signal-row">
      ${dot(r2Ok)}
      <span>Güçlü sinyal: ${ozet?.guclu_sinyal||0} / 150 hisse</span>
    </div>

    <!-- Kilitli ek sinyaller -->
    <div style="position:relative;overflow:hidden;margin-top:6px;border-radius:4px;max-height:38px">
      <div style="filter:blur(3.5px);user-select:none;pointer-events:none">
        <div class="signal-row"><div class="signal-dot g"></div><span>Long Sinyali: ••</span></div>
        <div class="signal-row"><div class="signal-dot r"></div><span>Short Sinyali: ••</span></div>
        <div class="signal-row"><div class="signal-dot o"></div><span>Kırılım Alarmı: ••</span></div>
        <div class="signal-row"><div class="signal-dot" style="background:#70a8ff"></div><span>Formasyon: ••</span></div>
        <div class="signal-row"><div class="signal-dot c"></div><span>Momentum Geçiş: ••</span></div>
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

    <!-- ELITE -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#8b5cf6;letter-spacing:1px;text-transform:uppercase">ELİTE</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#8b5cf6">ELITE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺599 /ay</span>
        </div>
        <div style="background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#8b5cf6;margin-bottom:5px">
          👑 Günde 4 · Aylık 120 rapor (~4,99₺ / rapor)
        </div>
        <button onclick="openExampleModal('elite')" style="display:block;width:100%;margin-bottom:7px;background:#7c3aed;border:none;border-radius:4px;padding:6px;color:#fff;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Tam sayfa Derinlemesine ALGORİTMİK uzman analizi</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Kapsamlı SMR yapı-anatomi-kurumsal ayak izi değerlendirmesi</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her işlem günü 19:00 bülteni (XU100)</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>631 BIST · 84 kripto · 6 emtia</span></div>
        </div>
        <!-- HER HAFTA SONU -->
        <div style="margin-top:8px;background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.2);border-radius:4px;padding:7px 8px">
          <div style="font-size:9px;font-weight:800;color:#8b5cf6;letter-spacing:0.8px;margin-bottom:3px">🗓 HER HAFTA SONU</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);margin-bottom:2px">Hafta Sonu Özel Tarama Özeti</div>
          <div style="font-size:9.5px;color:var(--text-muted);margin-bottom:8px;line-height:1.4">Tüm BIST taranır — yalnızca en sıkı filtreden geçenler listelenir.</div>
          <div style="display:flex;flex-direction:column;gap:4px">
            <div style="font-size:9px;letter-spacing:1px;color:#94a3b8;font-weight:700;margin-bottom:1px">📈 SENTİMENT &amp; MOMENTUM TARAMALARI</div>
            <div style="background:rgba(255,215,0,0.1);border:1px solid rgba(255,215,0,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ffd700;font-weight:700">🔵 MAVİYE YOLCULUK · dönmeye başlayanlar</div>
            <div style="background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#8b5cf6;font-weight:700">🚀 POTANSİYEL KALKIŞ · satış baskısı azalanlar</div>
            <div style="background:rgba(255,100,50,0.1);border:1px solid rgba(255,100,50,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ff8c5a;font-weight:700">💪 MAVİDE GÜÇLENEN · yükselişi güçlenenler</div>
          </div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
      </div>
    </div>

    <!-- PRO -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">PRO</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">PRO</span>
          <span style="font-size:11px;color:var(--text-muted)">₺349 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:5px">
          📊 Günde 2 · Aylık 60 rapor (~5,82₺ / rapor)
        </div>
        <button onclick="openExampleModal('pro')" style="display:block;width:100%;margin-bottom:7px;background:#2563eb;border:none;border-radius:4px;padding:6px;color:#fff;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Algoritmik Teknik Analiz Özeti</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Tam sayfa 7 maddelik Detaylı Teknik Analiz (PA + ICT) Kart</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her işlem günü 19:00 bülteni (XU100)</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>631 BIST · 84 kripto · 6 emtia</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
      </div>
    </div>

    <!-- FREE -->
    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#00e676;letter-spacing:1px;text-transform:uppercase">ÜCRETSİZ</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#00e676">FREE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺0 /ay</span>
        </div>
        <div style="background:rgba(0,230,118,0.1);border:1px solid rgba(0,230,118,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#00e676;margin-bottom:5px">
          📊 Günde 1 hisse kartı (aylık 30)
        </div>
        <button onclick="openExampleModal('free')" style="display:block;width:100%;margin-bottom:7px;background:#00c853;border:none;border-radius:4px;padding:6px;color:#000;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Algoritmik Teknik Analiz Özeti</strong></span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
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
      akıllı para anomalileri ve haftalık TIER-1 tarama raporları.
    </div>
    <div class="cta-buttons">
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="btn-elite">👑 Telegram ELITE — Günde 4 · Aylık 120 Rapor</a>
      <a href="#" onclick="document.getElementById('plans-modal').style.display='block';document.body.style.overflow='hidden';return false;" class="btn-pro">🔵 Telegram PRO — Günde 2 · Aylık 60 Rapor</a>
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
        <div style="font-size:9px;font-weight:700;color:#8b5cf6;letter-spacing:1px;text-transform:uppercase">ELİTE</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#8b5cf6">ELITE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺599 /ay</span>
        </div>
        <div style="background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#8b5cf6;margin-bottom:5px">
          👑 Günde 4 · Aylık 120 rapor (~4,99₺ / rapor)
        </div>
        <button onclick="openExampleModal('elite')" style="display:block;width:100%;margin-bottom:7px;background:#7c3aed;border:none;border-radius:4px;padding:6px;color:#fff;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Tam sayfa Derinlemesine ALGORİTMİK uzman analizi</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Kapsamlı SMR yapı-anatomi-kurumsal ayak izi değerlendirmesi</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her işlem günü 19:00 bülteni (XU100)</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>631 BIST · 84 kripto · 6 emtia</span></div>
        </div>
        <div style="margin-top:8px;background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.2);border-radius:4px;padding:7px 8px">
          <div style="font-size:9px;font-weight:800;color:#8b5cf6;letter-spacing:0.8px;margin-bottom:3px">🗓 HER HAFTA SONU</div>
          <div style="font-size:11px;font-weight:700;color:var(--text);margin-bottom:2px">Hafta Sonu Özel Tarama Özeti</div>
          <div style="font-size:9.5px;color:var(--text-muted);margin-bottom:8px;line-height:1.4">Tüm BIST taranır — yalnızca en sıkı filtreden geçenler listelenir.</div>
          <div style="display:flex;flex-direction:column;gap:4px">
            <div style="font-size:9px;letter-spacing:1px;color:#94a3b8;font-weight:700;margin-bottom:1px">📈 SENTİMENT &amp; MOMENTUM TARAMALARI</div>
            <div style="background:rgba(255,215,0,0.1);border:1px solid rgba(255,215,0,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ffd700;font-weight:700">🔵 MAVİYE YOLCULUK · dönmeye başlayanlar</div>
            <div style="background:rgba(139,92,246,0.1);border:1px solid rgba(139,92,246,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#8b5cf6;font-weight:700">🚀 POTANSİYEL KALKIŞ · satış baskısı azalanlar</div>
            <div style="background:rgba(255,100,50,0.1);border:1px solid rgba(255,100,50,0.25);border-radius:3px;padding:3px 7px;font-size:9.5px;color:#ff8c5a;font-weight:700">💪 MAVİDE GÜÇLENEN · yükselişi güçlenenler</div>
          </div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
      </div>
    </div>

    <hr class="tg-divider">

    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#70a8ff;letter-spacing:1px;text-transform:uppercase">PRO</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#70a8ff">PRO</span>
          <span style="font-size:11px;color:var(--text-muted)">₺349 /ay</span>
        </div>
        <div style="background:rgba(70,130,255,0.1);border:1px solid rgba(70,130,255,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#70a8ff;margin-bottom:5px">
          📊 Günde 2 · Aylık 60 rapor (~5,82₺ / rapor)
        </div>
        <button onclick="openExampleModal('pro')" style="display:block;width:100%;margin-bottom:7px;background:#2563eb;border:none;border-radius:4px;padding:6px;color:#fff;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Algoritmik Teknik Analiz Özeti</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Tam sayfa 7 maddelik Detaylı Teknik Analiz (PA + ICT) Kart</strong></span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Her işlem günü 19:00 bülteni (XU100)</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>631 BIST · 84 kripto · 6 emtia</span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
      </div>
    </div>

    <hr class="tg-divider">

    <div class="tg-tier-card">
      <div style="padding:8px 10px 0">
        <div style="font-size:9px;font-weight:700;color:#00e676;letter-spacing:1px;text-transform:uppercase">ÜCRETSİZ</div>
        <div style="display:flex;align-items:baseline;gap:6px;margin:2px 0 6px">
          <span style="font-size:20px;font-weight:900;color:#00e676">FREE</span>
          <span style="font-size:11px;color:var(--text-muted)">₺0 /ay</span>
        </div>
        <div style="background:rgba(0,230,118,0.1);border:1px solid rgba(0,230,118,0.25);border-radius:3px;padding:3px 7px;font-size:10px;color:#00e676;margin-bottom:5px">
          📊 Günde 1 hisse kartı (aylık 30)
        </div>
        <button onclick="openExampleModal('free')" style="display:block;width:100%;margin-bottom:7px;background:#00c853;border:none;border-radius:4px;padding:6px;color:#000;font-size:10px;font-weight:800;cursor:pointer;letter-spacing:0.2px">📸 ÖRNEK ÇIKTI İÇİN TIKLAYIN</button>
        <div class="tg-feature-list">
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span>Momentum-Akıllı Para &amp; Sentiment Grafikleri</span></div>
          <div class="tg-feature-row"><span class="tg-feature-icon">•</span><span><strong>Algoritmik Teknik Analiz Özeti</strong></span></div>
        </div>
      </div>
      <div style="padding:8px 10px">
        <div style="display:block;width:100%;text-align:center;background:rgba(100,116,139,0.1);border:1px solid #334155;border-radius:7px;padding:8px;color:#64748b;font-size:12px;font-weight:700;cursor:default">🕐 ÇOK YAKINDA</div>
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

// ── Hisse Arama ─────────────────────────────────────────────────────────────────────────────
function handleStockSearch() {
  var input  = document.getElementById('stock-search-input');
  var result = document.getElementById('stock-search-result');
  if (!input || !result) return;
  var ticker = input.value.trim().toUpperCase().replace(/\.IS$/i, '');
  if (!ticker) { result.innerHTML = ''; return; }
  var erData = window._erkenRadarData;
  if (erData && erData.public_items) {
    var found = erData.public_items.find(function(it) {
      return it.ticker.replace('.IS','').toUpperCase() === ticker;
    });
    if (found) {
      var catIcons = { A: '🔄', B: '📐', C: '🚀', D: '⚠️' };
      var icon = catIcons[found.category] || '🎯';
      result.innerHTML = icon + ' <strong>' + ticker + '</strong>'
        + ' — <span style="color:#7dd3fc;">' + found.scenario_name + '</span>'
        + ' <span style="color:#fbbf24;">★★★★★</span>';
      return;
    }
  }
  result.innerHTML = 'Detaylı analiz için Algoritmanın Telegram Botu’na <strong>#'
    + ticker + '</strong> yazabilirsiniz.'
    + ' ⏳ <span style="color:#f59e0b;font-weight:600;">Çok yakında başlıyoruz.</span>';
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

// ── ABONE SAYACI ──────────────────────────────────────────────────────────────
function renderUserCounter(meta) {
  const el = document.getElementById('user-counter-strip');
  if (!el) return;
  let timeStr = '';
  if (meta && meta.canli_guncelleme) {
    timeStr = ' &nbsp;·&nbsp; Piyasa verisi <strong>' + meta.canli_guncelleme + '</strong> güncellendi';
  }
  el.innerHTML = `<div class="bot-status-strip">🟢 <strong>Algoritma aktif</strong>${timeStr}</div>`;
}
