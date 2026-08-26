#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Borsa İstanbul'un resmî pazar tablosunu V4 evren dosyasına çevirir.

Bu araç yalnızca pazar sınıfını çıkarır. VBTS / brüt takas gibi günlük
tedbirler ayrıca doğrulanmadıkça boş bırakılır; V4 bu durumda işlem önermez.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


MARKET_COLUMNS = {"YILDIZ PAZAR": "YILDIZ", "ANA PAZAR": "ANA", "ALT PAZAR": "ALT"}


def build_status(source_xlsx: Path) -> dict:
    frame = pd.read_excel(source_xlsx, sheet_name="Sheet1")
    markets: dict[str, str] = {}
    headers = list(frame.columns)
    starts: list[tuple[int, str]] = []
    for position, header in enumerate(headers):
        if str(header).strip().upper() in MARKET_COLUMNS:
            starts.append((position, MARKET_COLUMNS[str(header).strip().upper()]))
    if len(starts) != len(MARKET_COLUMNS):
        raise ValueError("Resmî tabloda Yıldız, Ana ve Alt pazar başlıkları birlikte bulunamadı.")
    # Resmî Excel'de her pazar, bir başlık ve onu izleyen adsız yardımcı
    # sütunlardan oluşur. Bir sonraki pazar başlığına kadar olan tüm sütunlar
    # aynı pazara aittir; sadece ilk sütunu almak kapsam hatası olur.
    for index, (start, market) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(headers)
        for column in frame.iloc[:, start:end].columns:
            for value in frame[column].dropna():
                ticker = str(value).strip().upper()
                if ticker and ticker != "NAN":
                    markets[ticker] = market
    if not markets:
        raise ValueError("Resmî tablodan hiç hisse kodu okunamadı.")
    return {
        "source": "https://www.borsaistanbul.com/files/pazar-degisikligi-tablosu-19062026.xlsx",
        "effective_date": "2026-07-01",
        "generated_from": source_xlsx.name,
        "markets": dict(sorted(markets.items())),
        "restrictions": {},
        "restriction_notice": "Tedbir listesi bu dosyada yoktur; canlı 17:35 evresinde resmî günlük tedbir doğrulaması zorunludur.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resmî BIST pazar tablosunu V4 evren JSON'una çevirir")
    parser.add_argument("--source", default="kapanis_oncesi_pazar_20260701.xlsx")
    parser.add_argument("--output", default="kapanis_oncesi_resmi_durum.json")
    args = parser.parse_args()
    result = build_status(Path(args.source))
    output = Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(result['markets'])} hisse için resmî pazar durumu yazıldı: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
