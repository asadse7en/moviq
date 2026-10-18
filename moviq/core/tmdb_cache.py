import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .config import TMDB_CACHE_FILE

CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
FAILURE_TTL_SECONDS = 60 * 60
MAX_CACHE_ENTRIES = 20000

log = logging.getLogger(__name__)


class TMDBCache:
    """Bounded SQLite cache for complete TMDB response data."""

    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._writes_since_eviction = 0
        self._connection = sqlite3.connect(
            self.path, timeout=5, check_same_thread=False,
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tmdb_cache (
                cache_key TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                ttl_seconds INTEGER NOT NULL,
                response_json TEXT NOT NULL
            )
            """,
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tmdb_cache_timestamp
            ON tmdb_cache(timestamp)
            """,
        )
        self._connection.commit()

    @staticmethod
    def key(path, params):
        safe_params = {
            name: value
            for name, value in params.items()
            if name.casefold() not in ("api_key", "access_token")
        }
        serialized = json.dumps(
            {"path": path, "params": safe_params},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    @staticmethod
    def _has_complete_metadata(path, data):
        if not isinstance(data, dict):
            return False
        if "results" in data:
            results = data["results"]
            if not isinstance(results, list):
                return False
            return any(
                isinstance(result, dict)
                and any(
                    isinstance(result.get(name), str)
                    and result[name].strip()
                    for name in ("title", "name", "original_title",
                                 "original_name")
                )
                for result in results
            )
        if path.startswith("/movie/"):
            return isinstance(data.get("title"), str) and bool(
                data["title"].strip()
            )
        if path.startswith("/tv/") and "/season/" not in path:
            return isinstance(data.get("name"), str) and bool(
                data["name"].strip()
            )
        return bool(data)

    def get(self, key):
        with self._lock:
            row = self._connection.execute(
                """
                SELECT timestamp, ttl_seconds, response_json
                FROM tmdb_cache WHERE cache_key = ?
                """,
                (key,),
            ).fetchone()
            if row is None:
                return False, None
            timestamp, ttl_seconds, response_json = row
            if timestamp + ttl_seconds <= self._clock():
                self._connection.execute(
                    "DELETE FROM tmdb_cache WHERE cache_key = ?", (key,),
                )
                self._connection.commit()
                return False, None
            try:
                return True, json.loads(response_json)
            except json.JSONDecodeError:
                log.warning("Removing invalid TMDB cache entry %s.", key)
                self._connection.execute(
                    "DELETE FROM tmdb_cache WHERE cache_key = ?", (key,),
                )
                self._connection.commit()
                return False, None

    def set(self, key, path, data):
        response_json = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"),
        )
        ttl_seconds = (
            CACHE_TTL_SECONDS
            if self._has_complete_metadata(path, data)
            else FAILURE_TTL_SECONDS
        )
        with self._lock:
            self._connection.execute(
                """
                INSERT OR REPLACE INTO tmdb_cache
                    (cache_key, path, timestamp, ttl_seconds, response_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (key, path, int(self._clock()), ttl_seconds, response_json),
            )
            self._writes_since_eviction += 1
            if self._writes_since_eviction >= 100:
                self._connection.execute(
                    """
                    DELETE FROM tmdb_cache
                    WHERE cache_key IN (
                        SELECT cache_key FROM tmdb_cache
                        ORDER BY timestamp DESC
                        LIMIT -1 OFFSET ?
                    )
                    """,
                    (MAX_CACHE_ENTRIES,),
                )
                self._writes_since_eviction = 0
            self._connection.commit()

    def purge_expired(self):
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM tmdb_cache WHERE timestamp + ttl_seconds <= ?",
                (self._clock(),),
            )
            self._connection.commit()
            return cursor.rowcount

    def clear(self):
        with self._lock:
            cursor = self._connection.execute("DELETE FROM tmdb_cache")
            self._connection.commit()
            return cursor.rowcount

    def close(self):
        with self._lock:
            self._connection.close()


_cache = None
_cache_initialization_failed = False
_cache_lock = threading.Lock()


def get_cache():
    """Return the shared on-disk cache, or None if storage is unavailable."""
    global _cache, _cache_initialization_failed
    with _cache_lock:
        if _cache is not None:
            return _cache
        if _cache_initialization_failed:
            return None
        try:
            cache = TMDBCache(TMDB_CACHE_FILE)
            cache.purge_expired()
            _cache = cache
        except (OSError, sqlite3.Error) as error:
            if "cache" in locals():
                cache.close()
            _cache_initialization_failed = True
            log.warning("TMDB response cache is unavailable: %s", error)
            return None
        return _cache


def clear_tmdb_cache():
    cache = get_cache()
    if cache is None:
        raise RuntimeError("TMDB response cache is unavailable.")
    return cache.clear()
