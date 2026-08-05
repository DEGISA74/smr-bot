#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bağımsız BIST Veri Sağlığı ekranı: streamlit run bist_data_monitor.py"""
import streamlit as st

from bist_data_status import collect_status

st.set_page_config(page_title="BIST Veri Sağlığı", page_icon="🛡️", layout="wide")
st.title("🛡️ BIST Veri Sağlığı")
if st.button("Yenile"):
    st.cache_data.clear()


@st.cache_data(ttl=10)
def _status():
    return collect_status()


s = _status()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Aktif sürüm", s.get("active_version", "yok"))
c2.metric("Sembol", s.get("symbols", 0))
c3.metric("Sürüm yaşı", f"{s.get('age_minutes', '?')} dk")
c4.metric("Karantina", s.get("quarantine", 0))

st.subheader("Son bar dağılımı")
st.json(s.get("latest_bar_distribution", {}), expanded=False)

st.subheader("Sağlayıcı trafik polisi")
rows = []
for name, p in s.get("provider_traffic", {}).get("providers", {}).items():
    rows.append({"Sağlayıcı": name, "Durum": p.get("last_status"),
                 "Başarı": p.get("success", 0), "Hata": p.get("failure", 0),
                 "Ortak bekleme (sn)": p.get("cooldown_remaining_sec", 0),
                 "Son hata": p.get("last_error", "")})
st.dataframe(rows, use_container_width=True, hide_index=True)

st.subheader("Son karantinalar")
st.dataframe(s.get("recent_quarantine", []), use_container_width=True,
             hide_index=True)
st.caption(f"Geçici hacimli sembol: {s.get('provisional_symbols', 0)} · "
           f"Uyarılı sembol: {s.get('warned_symbols', 0)} · "
           f"Aktif turda reddedilen: {s.get('active_rejected', 0)}")
