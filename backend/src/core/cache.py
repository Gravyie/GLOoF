"""Small thread-safe TTL cache with per-key locking.

Raster analysis takes seconds and Sentinel-2 only revisits every ~5 days, so
we cache results. The per-key lock stops a burst of identical requests from
each downloading the same rasters (cache stampede).
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Callable, Hashable, TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class TTLStore:
    def __init__(self, ttl_seconds: int, maxsize: int = 256) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = defaultdict(threading.Lock)

    def _peek(self, key: Hashable):
        with self._lock:
            return self._cache.get(key, None)

    def get_or_compute(self, key: Hashable, fn: Callable[[], T], *, force: bool = False) -> T:
        if not force:
            hit = self._peek(key)
            if hit is not None:
                return hit
        with self._lock:
            key_lock = self._key_locks[key]
        with key_lock:
            if not force:
                hit = self._peek(key)
                if hit is not None:
                    return hit
            value = fn()
            with self._lock:
                self._cache[key] = value
            return value
