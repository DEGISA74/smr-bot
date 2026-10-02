#!/usr/bin/env python3
"""
site_paylas_kart.py — smartmoneyradar.app PAYLAŞ kartı (2 Eki 2026).

Ziyaretçi analizini X'te paylaşınca linkin altında görsel çıksın diye her hisse için:
  kart/<T>.png   — "Akıllı Para İvmesi & Fiyat" grafiği (1200x630, X büyük kart boyu)
  p/<T>.html     — X'in okuduğu paylaşım sayfası (og/twitter etiketleri) → insanı
                   /?h=<T>&d=<kod> adresine yönlendirir (davet kodu korunur).
Veri: site_hisse_uretici'nin o turda yazdığı hisse JSON'u — EK VERİ OKUMASI YOK.
Çağıran: site_hisse_uretici.main (her hisse JSON'undan hemen sonra). Hata siteyi bozmaz.
"""
import html
import os
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SITE = "https://smartmoneyradar.app"
BG = "#0b1220"


def _tr(x: float, nd: int = 2) -> str:
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def ozet_cumle(j: dict) -> str:
    """Kartta ve paylaşım sayfasında aynı cümle (ölçüm, tahmin değil)."""
    t = j["hisse"]["ticker"]
    g = (j.get("akilli") or {}).get("guc20")
    if g is None or t == "XU100":
        return f"{t} · Akıllı Para İvmesi & Fiyat"
    if g >= 0:
        return f"Son 20 gün: XU100'ü {_tr(abs(g), 1)} puan geçti"
    return f"Son 20 gün: XU100'ün {_tr(abs(g), 1)} puan gerisinde"


def kart_ciz(j: dict, out_png: str) -> bool:
    g = j.get("grafik") or []
    h = j.get("hisse") or {}
    if len(g) < 5 or not h.get("ticker"):
        return False
    fig = plt.figure(figsize=(12, 6.3), dpi=100, facecolor=BG)
    try:
        ax = fig.add_axes([0.06, 0.13, 0.86, 0.62], facecolor=BG)
        ax2 = ax.twinx()
        x = list(range(len(g)))
        mf = [r.get("mf") or 0 for r in g]
        pr = [r.get("price") for r in g]
        ax.bar(x, mf, color=["#4f7cc9" if v >= 0 else "#e5484d" for v in mf], width=0.72, zorder=2)
        ax.axhline(0, color="#334155", lw=1)
        ax2.plot(x, pr, color="#f1f5f9", lw=2.4, zorder=3)
        for A in (ax, ax2):
            A.tick_params(colors="#94a3b8", labelsize=11)
            for s in A.spines.values():
                s.set_visible(False)
        ax.grid(axis="y", color="#1e293b", lw=.8)
        ax.set_xticks(x[::3])
        ax.set_xticklabels([g[i].get("date", "") for i in x[::3]])

        t = h["ticker"]
        fig.text(.06, .90, t, color="#ffffff", fontsize=34, fontweight="bold")
        k, d = h.get("kapanis"), h.get("degisim_pct")
        if k is not None:
            fiyat = _tr(k)
            if d is not None:
                fiyat += f"   {'▲' if d >= 0 else '▼'} %{_tr(abs(d))}"
            fig.text(.06 + len(t) * .027 + .02, .905, fiyat,
                     color="#22c55e" if (d or 0) >= 0 else "#f87171", fontsize=20, fontweight="bold")
        fig.text(.06, .825, "Akıllı Para İvmesi & Fiyat · son 30 gün", color="#38bdf8",
                 fontsize=15, fontweight="bold")
        gg = (j.get("akilli") or {}).get("guc20")
        if gg is not None and t != "XU100":
            fig.text(.94, .905, ozet_cumle(j), color="#22c55e" if gg >= 0 else "#f87171",
                     fontsize=15, ha="right", fontweight="bold")
        fig.text(.94, .835, "mavi = akıllı para giriyor · kırmızı = çıkıyor", color="#64748b",
                 fontsize=12, ha="right")
        fig.text(.06, .035, "SMART MONEY RADAR", color="#38bdf8", fontsize=14, fontweight="bold")
        fig.text(.94, .035, "smartmoneyradar.app · Algoritmanın gözünden", color="#94a3b8",
                 fontsize=13, ha="right")
        tmp = out_png + ".tmp.png"
        fig.savefig(tmp, facecolor=BG)
        os.replace(tmp, out_png)
        return True
    finally:
        plt.close(fig)


_SAYFA = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{baslik}</title>
<meta name="description" content="{aciklama}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Smart Money Radar">
<meta property="og:title" content="{baslik}">
<meta property="og:description" content="{aciklama}">
<meta property="og:image" content="{gorsel}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:url" content="{url}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{baslik}">
<meta name="twitter:description" content="{aciklama}">
<meta name="twitter:image" content="{gorsel}">
<style>body{{background:#0b1220;color:#e2e8f0;font-family:system-ui,sans-serif;text-align:center;padding:60px 16px}}a{{color:#38bdf8}}</style>
</head><body>
<p>{baslik} açılıyor… <a id="git" href="/?h={t}">Açılmazsa tıkla</a></p>
<script>
(function(){{var u="/?h={t}";try{{var d=new URLSearchParams(location.search).get("d");
if(d)u+="&d="+encodeURIComponent(d);}}catch(e){{}}
document.getElementById("git").href=u;location.replace(u);}})();
</script>
</body></html>
"""


def paylas_sayfasi(j: dict, out_html: str) -> None:
    t = j["hisse"]["ticker"]
    baslik = html.escape(f"{t} · Akıllı Para İvmesi | Smart Money Radar")
    aciklama = html.escape(ozet_cumle(j) + " · Algoritmanın gözünden bak.")
    gorsel = f"{SITE}/kart/{t}.png?v={int(time.time())}"
    s = _SAYFA.format(baslik=baslik, aciklama=aciklama, gorsel=gorsel,
                      url=f"{SITE}/p/{t}.html", t=t)
    tmp = out_html + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(s)
    os.replace(tmp, out_html)


def uret(j: dict, kok: str) -> bool:
    """kok = site kökü (/var/www/smr/frontend). Kart çizilemezse sayfa da yazılmaz."""
    t = j["hisse"]["ticker"]
    os.makedirs(os.path.join(kok, "kart"), exist_ok=True)
    os.makedirs(os.path.join(kok, "p"), exist_ok=True)
    if not kart_ciz(j, os.path.join(kok, "kart", f"{t}.png")):
        return False
    paylas_sayfasi(j, os.path.join(kok, "p", f"{t}.html"))
    return True
