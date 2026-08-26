# -*- coding: utf-8 -*-
"""Master Scan ilerleme yüzdesini gerçek geçmiş çalışma sürelerinden öğrenir.

Bu modül yalnızca kendi küçük zamanlama dosyasına yazar. Tarama sonucu, patron.db,
parquet veya hesap formüllerine dokunmaz. İlk çalışmada eşit adım payı kullanır;
sonraki çalışmalarda aynı kategori için son başarılı ölçümlerin medyan süresini
kullanır. Bir adım tamamlanmadan ilerleme payı verilmez.
"""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
PROFILE_PATH = ROOT / "master_scan_timing_profile.json"
MAX_SAMPLES = 8
PROFILE_VERSION = 1


def _clean_category(category: object) -> str:
    return str(category or "varsayilan").strip() or "varsayilan"


def _load() -> dict:
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == PROFILE_VERSION:
            return data
    except Exception:
        pass
    return {"version": PROFILE_VERSION, "categories": {}}


def _atomic_save(data: dict) -> None:
    temp = PROFILE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(PROFILE_PATH)


def _format_seconds(value: float | None) -> str:
    if value is None or value <= 0:
        return "ilk ölçüm"
    seconds = int(round(value))
    if seconds < 60:
        return f"~{seconds} sn"
    return f"~{seconds // 60} dk {seconds % 60:02d} sn"


class MasterScanProgress:
    """Adım tamamlandıkça ilerleyen, geçmiş taramalardan süre payı öğrenen sayaç."""

    def __init__(self, category: object, steps: Iterable[str]):
        self.category = _clean_category(category)
        self.steps = tuple(steps)
        self.data = _load()
        self.category_data = self.data.setdefault("categories", {}).setdefault(
            self.category, {"completed_runs": 0, "steps": {}}
        )
        self.started_key: str | None = None
        self.started_at: float | None = None
        self.completed: set[str] = set()
        self.run_durations: dict[str, float] = {}

    def _samples(self, key: str) -> list[float]:
        raw = self.category_data.get("steps", {}).get(key, [])
        return [float(x) for x in raw if isinstance(x, (int, float)) and float(x) > 0]

    def estimate_seconds(self, key: str) -> float | None:
        samples = self._samples(key)
        return float(statistics.median(samples)) if samples else None

    def _weight(self, key: str) -> float:
        # İlk run'da adımlar eşit pay alır; yeterli kayıt oluşunca gerçek medyan süre kullanılır.
        return self.estimate_seconds(key) or 1.0

    def _percent(self) -> int:
        total = sum(self._weight(key) for key in self.steps) or 1.0
        done = sum(self._weight(key) for key in self.steps if key in self.completed)
        return min(99, max(0, int(round(99 * done / total))))

    def _hint(self, key: str) -> str:
        estimate = self.estimate_seconds(key)
        if estimate is None:
            return "süre öğreniliyor"
        return f"son {len(self._samples(key))} tarama medyanı {_format_seconds(estimate)}"

    def _record_previous(self) -> None:
        if self.started_key is None or self.started_at is None:
            return
        elapsed = max(0.01, time.perf_counter() - self.started_at)
        self.run_durations[self.started_key] = elapsed
        self.completed.add(self.started_key)
        self.started_key = None
        self.started_at = None

    def begin(self, key: str, label: str) -> tuple[int, str]:
        """Önceki adımı gerçek süresiyle kapatır, yeni adımı başlatır."""
        self._record_previous()
        self.started_key = key
        self.started_at = time.perf_counter()
        return self._percent(), f"⏳ {label} · {self._hint(key)}"

    def finish(self) -> tuple[int, str]:
        """Son adımı kapatır ve bu run'ın sürelerini ileriye dönük profile ekler."""
        self._record_previous()
        steps = self.category_data.setdefault("steps", {})
        for key, elapsed in self.run_durations.items():
            samples = list(steps.get(key, []))
            samples.append(round(float(elapsed), 3))
            steps[key] = samples[-MAX_SAMPLES:]
        if self.run_durations:
            self.category_data["completed_runs"] = int(self.category_data.get("completed_runs", 0)) + 1
            _atomic_save(self.data)
        return 100, "✅ TARAMA TAMAMLANDI! Sonuçlar yükleniyor...%100"
