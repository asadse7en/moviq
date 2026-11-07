import io
import logging
import threading
import time
import traceback
import unittest
from unittest.mock import Mock, patch

import requests

from moviq.core import network_state
from moviq.core.tmdb import (
    REQUEST_TIMEOUT,
    TMDB,
    TMDBAuthError,
    TMDBError,
    _TokenBucket,
)


class FakeCache:
    def __init__(self):
        self.entries = {}

    def key(self, path, params):
        return path, tuple(sorted(params.items()))

    def get(self, key):
        return (key in self.entries, self.entries.get(key))

    def set(self, key, _path, value):
        self.entries[key] = value


class FakeResponse:
    def __init__(self, status, data=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._data = data

    def json(self):
        return self._data


class TMDBHTTPTests(unittest.TestCase):
    def setUp(self):
        network_state.reset()
        self.addCleanup(network_state.reset)
        self.cache = FakeCache()
        self.cache_patch = patch(
            "moviq.core.tmdb.get_cache",
            return_value=self.cache,
        )
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.limiter = Mock()
        self.limiter.acquire = Mock()
        self.limiter.pause_for = Mock()
        self.limiter_patch = patch(
            "moviq.core.tmdb._REQUEST_LIMITER",
            self.limiter,
        )
        self.limiter_patch.start()
        self.addCleanup(self.limiter_patch.stop)

    def test_429_honors_retry_after_and_retries(self):
        with patch(
            "moviq.core.tmdb._SESSION.get",
            side_effect=[
                FakeResponse(429, headers={"Retry-After": "2"}),
                FakeResponse(200, {"results": [{"id": 1}]}),
            ],
        ) as request:
            result = TMDB("test-key").get("/search/movie", query="Film")

        self.assertEqual(result, {"results": [{"id": 1}]})
        self.assertEqual(request.call_count, 2)
        self.limiter.pause_for.assert_called_once()
        self.assertGreaterEqual(
            self.limiter.pause_for.call_args.args[0],
            2,
        )
        self.assertEqual(request.call_args.kwargs["timeout"], REQUEST_TIMEOUT)

    def test_401_and_404_are_not_retried(self):
        for status in (401, 404):
            with self.subTest(status=status):
                with patch(
                    "moviq.core.tmdb._SESSION.get",
                    return_value=FakeResponse(status),
                ) as request:
                    client = TMDB("secret-key")
                    if status == 401:
                        with self.assertRaises(TMDBAuthError) as raised:
                            client.get("/search/movie", query="Unique401")
                        self.assertIn("Invalid TMDB API key", str(raised.exception))
                    else:
                        self.assertIsNone(
                            client.get("/search/movie", query="Unique404"),
                        )
                request.assert_called_once()

    def test_network_error_retries_and_redacts_credentials(self):
        secret = "secret-api-key"
        request_error = requests.ConnectionError(
            f"https://example.invalid/?api_key={secret}",
        )
        output = io.StringIO()
        logs = io.StringIO()
        handler = logging.StreamHandler(logs)
        logger = logging.getLogger("moviq.core.tmdb")
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        with patch(
            "moviq.core.tmdb._SESSION.get",
            side_effect=request_error,
        ) as request, patch.object(TMDB, "_wait"), patch(
            "sys.stdout", output,
        ), patch("sys.stderr", output):
            try:
                TMDB(secret).get("/search/movie", query="UniqueNetwork")
            except TMDBError as error:
                error_output = str(error) + traceback.format_exc()
            else:
                self.fail("Expected the exhausted request to fail.")

        self.assertEqual(request.call_count, 4)
        self.assertNotIn(secret, error_output)
        self.assertNotIn(secret, output.getvalue())
        self.assertNotIn(secret, logs.getvalue())
        self.assertTrue(network_state.is_down())
        with patch("moviq.core.tmdb._SESSION.get") as retry_request:
            with self.assertRaisesRegex(TMDBError, "No internet connection"):
                TMDB(secret).get("/search/movie", query="UniqueFastFailure")
        retry_request.assert_not_called()

    def test_v4_token_is_sent_only_as_bearer_authorization(self):
        token = "v4-token-" + ("x" * 50)
        with patch(
            "moviq.core.tmdb._SESSION.get",
            return_value=FakeResponse(200, {"id": 1}),
        ) as request:
            TMDB(token).get("/movie/1")

        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], f"Bearer {token}")
        self.assertNotIn("api_key", kwargs["params"])

    def test_rate_limit_pauses_every_waiting_worker(self):
        limiter = _TokenBucket(rate=100, capacity=2)
        limiter.pause_for(0.15)
        barrier = threading.Barrier(3)
        acquired = []

        def worker():
            barrier.wait()
            limiter.acquire()
            acquired.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(2)]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=1)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(len(acquired), 2)
        self.assertTrue(all(timestamp - started >= 0.12 for timestamp in acquired))

    def test_api_key_is_not_part_of_the_persistent_cache_key(self):
        with patch(
            "moviq.core.tmdb._SESSION.get",
            return_value=FakeResponse(200, {"results": [{"id": 1}]}),
        ) as request:
            TMDB("secret-key").get("/search/movie", query="UniqueCacheKey")

        cache_key = next(iter(self.cache.entries))
        self.assertNotIn("api_key", dict(cache_key[1]))
        self.assertEqual(request.call_args.kwargs["params"]["api_key"], "secret-key")

    def test_cached_search_response_is_raw_metadata_not_a_match_decision(self):
        response_data = {
            "results": [
                {"id": 1, "title": "The Matrix"},
                {"id": 2, "title": "Matrix"},
            ],
        }
        with patch(
            "moviq.core.tmdb._SESSION.get",
            return_value=FakeResponse(200, response_data),
        ) as request:
            client = TMDB("secret-key")
            first = client.search("Matrix")
            second = client.search("Matrix")

        self.assertEqual(first, response_data["results"])
        self.assertEqual(second, response_data["results"])
        request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
