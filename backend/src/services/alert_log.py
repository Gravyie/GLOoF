"""Thread-safe in-memory alert log (audit trail for the demo / single process)."""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone
from typing import Any


class AlertLog:
    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def add(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            rec = {"id": next(self._ids), "ts": datetime.now(timezone.utc).isoformat(), **fields}
            self._items.append(rec)
            return dict(rec)

    def update(self, alert_id: int, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            for rec in self._items:
                if rec["id"] == alert_id:
                    rec.update(fields)
                    return dict(rec)
        return None

    def get(self, alert_id: int) -> dict[str, Any] | None:
        with self._lock:
            return next((dict(r) for r in self._items if r["id"] == alert_id), None)

    def list(self, *, lake: str | None = None, source: str | None = None, after_id: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._items
                    if r["id"] > after_id and (lake is None or r["lake"] == lake)
                    and (source is None or r.get("source") == source)]

    def clear(self, *, source: str | None = None) -> None:
        with self._lock:
            self._items = [r for r in self._items if source is not None and r.get("source") != source]
