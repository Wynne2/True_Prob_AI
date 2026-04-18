"""
Cache Service.

Provides two layers of caching:
  1. Disk cache  – JSON files under data/cache/<namespace>/<key>.json
                   Survives process restarts; ideal for daily pulls.
  2. In-memory   – Python dict with TTL; fast within a single run.

Usage
-----
    from services.cache_service import CacheService
    cache = CacheService(namespace="nba_api")
    cache.set("usage_2025-04-16", data, ttl_seconds=86400)
    data = cache.get("usage_2025-04-16")   # None on miss / expiry
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Root directory where all cache subdirectories live
_CACHE_ROOT = Path(__file__).parent.parent / "data" / "cache"


class CacheService:
    """
    Disk + in-memory cache with per-key TTL.

    Parameters
    ----------
    namespace : str
        Sub-directory under data/cache/ (e.g. "nba_api", "sportsdataio").
    default_ttl : int
        Default TTL in seconds (0 = never expire).
    """

    def __init__(self, namespace: str, default_ttl: int = 86_400) -> None:
        self._namespace = namespace
        self._default_ttl = default_ttl
        self._cache_dir = _CACHE_ROOT / namespace
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # In-memory store:  key -> (value, expires_at)
        self._mem: dict[str, tuple[Any, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Return cached value or None if missing / expired."""
        # Check memory first
        if key in self._mem:
            value, expires_at = self._mem[key]
            if expires_at == 0 or time.monotonic() < expires_at:
                return value
            del self._mem[key]

        # Fall back to disk
        path = self._disk_path(key)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    envelope = json.load(fh)
                expires_at = envelope.get("expires_at", 0)
                if expires_at == 0 or time.time() < expires_at:
                    value = envelope["value"]
                    # Promote to memory
                    mem_ttl = max(0.0, expires_at - time.time()) if expires_at else 0.0
                    self._mem[key] = (value, time.monotonic() + mem_ttl if mem_ttl else 0.0)
                    return value
                # Expired disk entry – remove
                path.unlink(missing_ok=True)
            except (json.JSONDecodeError, KeyError, OSError) as exc:
                logger.warning("Cache read error for %s/%s: %s", self._namespace, key, exc)
        return None

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Store *value* under *key* with the given TTL (seconds)."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        expires_at_wall = (time.time() + ttl) if ttl else 0
        expires_at_mono = (time.monotonic() + ttl) if ttl else 0.0

        # Memory
        self._mem[key] = (value, expires_at_mono)

        # Disk
        path = self._disk_path(key)
        try:
            envelope = {"expires_at": expires_at_wall, "value": value}
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(envelope, fh, default=str)
        except (OSError, TypeError) as exc:
            logger.warning("Cache write error for %s/%s: %s", self._namespace, key, exc)

    def invalidate(self, key: str) -> None:
        """Remove a single key from both memory and disk."""
        self._mem.pop(key, None)
        self._disk_path(key).unlink(missing_ok=True)

    def invalidate_stale(self) -> int:
        """Remove all expired disk entries; return count of purged files."""
        now = time.time()
        purged = 0
        for path in self._cache_dir.glob("*.json"):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    envelope = json.load(fh)
                expires_at = envelope.get("expires_at", 0)
                if expires_at and time.time() > expires_at:
                    path.unlink(missing_ok=True)
                    purged += 1
            except (json.JSONDecodeError, OSError):
                pass
        return purged

    def clear_namespace(self) -> None:
        """Wipe all entries for this namespace (disk + memory)."""
        self._mem.clear()
        for path in self._cache_dir.glob("*.json"):
            path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _disk_path(self, key: str) -> Path:
        # Sanitise the key so it is safe as a filename
        safe_key = "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
        return self._cache_dir / f"{safe_key}.json"


# ---------------------------------------------------------------------------
# Module-level convenience singletons
# ---------------------------------------------------------------------------

_singletons: dict[str, CacheService] = {}


def get_cache(namespace: str, default_ttl: int = 86_400) -> CacheService:
    """Return (or create) a shared CacheService for *namespace*."""
    if namespace not in _singletons:
        _singletons[namespace] = CacheService(namespace, default_ttl)
    return _singletons[namespace]
