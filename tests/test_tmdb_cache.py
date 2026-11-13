import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from moviq.core.tmdb_cache import (
    CACHE_TTL_SECONDS,
    FAILURE_TTL_SECONDS,
    TMDBCache,
    close_cache,
)


class TMDBCacheTests(unittest.TestCase):
    def test_close_cache_closes_initialized_shared_connection(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = TMDBCache(Path(temp.name) / "cache.sqlite3")

        with patch("moviq.core.tmdb_cache._cache", cache):
            close_cache()

        with self.assertRaises(sqlite3.ProgrammingError):
            cache._connection.execute("SELECT 1")

    def test_cache_key_does_not_include_api_credentials(self):
        first = TMDBCache.key(
            "/search/movie", {"query": "Inception", "api_key": "secret"},
        )
        second = TMDBCache.key(
            "/search/movie", {"query": "Inception", "api_key": "another"},
        )
        self.assertEqual(first, second)

    def test_complete_entries_expire_after_seven_days(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        now = [1_800_000_000]
        cache = TMDBCache(
            Path(temp.name) / "cache.sqlite3",
            clock=lambda: now[0],
        )
        self.addCleanup(cache.close)
        key = cache.key("/search/movie", {"query": "Inception"})
        cache.set(
            key,
            "/search/movie",
            {"results": [{"id": 1, "title": "Inception"}]},
        )

        row = cache._connection.execute(
            "SELECT timestamp, ttl_seconds FROM tmdb_cache",
        ).fetchone()
        self.assertEqual(row, (now[0], CACHE_TTL_SECONDS))
        self.assertEqual(
            cache.get(key),
            (True, {"results": [{"id": 1, "title": "Inception"}]}),
        )

        now[0] += CACHE_TTL_SECONDS
        self.assertEqual(cache.get(key), (False, None))

    def test_failed_lookups_expire_after_one_hour(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        now = [1_800_000_000]
        cache = TMDBCache(
            Path(temp.name) / "cache.sqlite3",
            clock=lambda: now[0],
        )
        self.addCleanup(cache.close)
        key = cache.key("/search/movie", {"query": "Missing"})
        cache.set(key, "/search/movie", {"results": []})

        row = cache._connection.execute(
            "SELECT ttl_seconds FROM tmdb_cache",
        ).fetchone()
        self.assertEqual(row, (FAILURE_TTL_SECONDS,))
        self.assertEqual(cache.get(key), (True, {"results": []}))
        missing_title_key = cache.key(
            "/search/movie", {"query": "Malformed"},
        )
        cache.set(
            missing_title_key,
            "/search/movie",
            {"results": [{}]},
        )
        missing_title_ttl = cache._connection.execute(
            "SELECT ttl_seconds FROM tmdb_cache WHERE cache_key = ?",
            (missing_title_key,),
        ).fetchone()[0]
        self.assertEqual(missing_title_ttl, FAILURE_TTL_SECONDS)

        now[0] += FAILURE_TTL_SECONDS
        self.assertEqual(cache.get(key), (False, None))

    def test_missing_detail_title_is_cached_briefly(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        cache = TMDBCache(Path(temp.name) / "cache.sqlite3")
        self.addCleanup(cache.close)
        key = cache.key("/movie/123", {})
        cache.set(key, "/movie/123", {"id": 123})

        row = cache._connection.execute(
            "SELECT ttl_seconds FROM tmdb_cache",
        ).fetchone()
        self.assertEqual(row, (FAILURE_TTL_SECONDS,))

    def test_expired_data_is_purged_on_startup(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "cache.sqlite3"
        now = [1_800_000_000]
        cache = TMDBCache(path, clock=lambda: now[0])
        self.addCleanup(cache.close)
        key = cache.key("/movie/1", {})
        cache.set(key, "/movie/1", {"title": "Example"})
        now[0] += CACHE_TTL_SECONDS + 1
        cache.close()

        reopened = TMDBCache(path, clock=lambda: now[0])
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.purge_expired(), 1)
        count = reopened._connection.execute(
            "SELECT COUNT(*) FROM tmdb_cache",
        ).fetchone()[0]
        self.assertEqual(count, 0)
