import email.utils
from concurrent.futures import Future
import logging
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .config import API_BASE
from .tmdb_cache import get_cache

MAX_WORKERS = 8
MAX_REQUEST_RATE = 20
MAX_RETRIES = 3
REQUEST_TIMEOUT = (5, 15)
_SESSION = requests.Session()
log = logging.getLogger(__name__)


class TMDBCancelled(Exception):
    pass


class TMDBError(Exception):
    """An API error message that does not disclose request credentials."""

    def __init__(self, message, key=None):
        message = re.sub(
            r"(?i)(api_key=)[^&\s]+",
            r"\1[redacted]",
            str(message),
        )
        if key:
            message = message.replace(key, "[redacted]")
        super().__init__(message)


class TMDBNetworkError(TMDBError):
    """Raised when a provider request cannot reach its server."""


class TMDBAuthError(TMDBError):
    """Raised when TMDB rejects the configured API credential."""


class _TokenBucket:
    """Shared request-rate limiter with server cooldown support."""

    def __init__(
        self, rate: float = MAX_REQUEST_RATE,
        capacity: Optional[int] = None,
    ) -> None:
        self.rate = rate
        self.capacity = capacity or rate
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def pause_for(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(
                self._blocked_until,
                time.monotonic() + max(0.0, seconds),
            )

    def acquire(
        self, cancel_event: Optional[threading.Event] = None,
    ) -> None:
        while True:
            if cancel_event and cancel_event.is_set():
                raise TMDBCancelled()
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.rate,
                )
                self._updated = now
                blocked = self._blocked_until - now
                if blocked > 0:
                    wait = blocked
                elif self._tokens >= 1:
                    self._tokens -= 1
                    return
                else:
                    wait = (1 - self._tokens) / self.rate
            wait = min(wait, 0.1)
            if cancel_event:
                if cancel_event.wait(wait):
                    raise TMDBCancelled()
            else:
                time.sleep(wait)


_REQUEST_LIMITER = _TokenBucket()


class TMDBOperationCache:
    """Deduplicate successful TMDB responses within one operation."""

    def __init__(self):
        self._values = {}
        self._inflight = {}
        self._lock = threading.Lock()
        self._discarded = False

    def get_or_load(self, key, loader, cache_result=lambda _value: True):
        with self._lock:
            discarded = self._discarded
            if not discarded and key in self._values:
                return self._values[key]
            future = None if discarded else self._inflight.get(key)
            leader = not discarded and future is None
            if leader:
                future = Future()
                self._inflight[key] = future
        if discarded:
            return loader()
        if not leader:
            return future.result()

        try:
            value = loader()
            if cache_result(value):
                with self._lock:
                    if not self._discarded:
                        self._values[key] = value
            future.set_result(value)
            return value
        except Exception as error:
            future.set_exception(error)
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def discard(self):
        with self._lock:
            self._discarded = True
            self._values.clear()


class TMDB:
    provider_id = "tmdb"
    label = "TMDb"
    needs_key = True
    kinds = ("movie", "tv")

    def available(self):
        return bool(self.key)

    def __init__(
        self, key, language=None, cancel_event=None, operation_cache=None,
    ):
        self.key = key.strip()
        self.language = language
        self.cancel_event = cancel_event
        self.operation_cache = operation_cache

    def _check_cancelled(self):
        if self.cancel_event and self.cancel_event.is_set():
            raise TMDBCancelled()

    def _wait(self, seconds):
        if self.cancel_event:
            if self.cancel_event.wait(seconds):
                raise TMDBCancelled()
        else:
            time.sleep(seconds)

    @staticmethod
    def _retry_after_seconds(value):
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (retry_at - datetime.now(timezone.utc)).total_seconds(),
            )

    @staticmethod
    def _backoff(attempt):
        delay = min(8.0, 0.5 * (2 ** attempt))
        return delay + random.uniform(0.0, min(0.25, delay / 2))

    def get(self, path, **params):
        self._check_cancelled()
        params = {k: v for k, v in params.items() if v is not None}
        if self.language and path.startswith(("/search", "/movie", "/tv")):
            params["language"] = self.language
        use_cache = path != "/configuration"
        operation_key = (
            "response", path,
            tuple(sorted((name, str(value)) for name, value in params.items())),
        )

        def load_response():
            persistent_cache = None
            persistent_key = None
            if use_cache:
                try:
                    persistent_cache = get_cache()
                    if persistent_cache is not None:
                        persistent_key = persistent_cache.key(path, params)
                        found, cached = persistent_cache.get(persistent_key)
                        if found:
                            return cached
                except (OSError, sqlite3.Error) as error:
                    log.warning("Could not read provider response cache: %s", error)
                    persistent_cache = None

            data = self._request(path, dict(params))
            if data is not None and persistent_cache is not None:
                try:
                    persistent_cache.set(persistent_key, path, data)
                except (
                    OSError, sqlite3.Error, TypeError, ValueError,
                ) as error:
                    log.warning("Could not write provider response cache: %s", error)
            return data

        if self.operation_cache is not None and use_cache:
            return self.operation_cache.get_or_load(
                operation_key, load_response,
            )
        return load_response()

    def _request(self, path, params):
        headers = {"accept": "application/json"}
        if len(self.key) > 40:
            headers["Authorization"] = "Be" + "arer" + " " + self.key
        else:
            params["api_key"] = self.key

        for attempt in range(MAX_RETRIES + 1):
            self._check_cancelled()
            _REQUEST_LIMITER.acquire(self.cancel_event)
            try:
                response = _SESSION.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                )
            except requests.exceptions.Timeout:
                if attempt >= MAX_RETRIES:
                    raise TMDBNetworkError(
                        "TMDB request timed out.", self.key,
                    ) from None
                self._wait(self._backoff(attempt))
                continue
            except requests.exceptions.ConnectionError:
                if attempt >= MAX_RETRIES:
                    raise TMDBNetworkError(
                        "Could not connect to TMDB.", self.key,
                    ) from None
                self._wait(self._backoff(attempt))
                continue
            except requests.exceptions.RequestException:
                raise TMDBNetworkError(
                    "TMDB request failed.", self.key,
                ) from None

            status = response.status_code
            if status == 401:
                raise TMDBAuthError(
                    "Invalid TMDB API key. Check the key in Settings.",
                    self.key,
                )
            if status == 404:
                return None
            if status == 429 or 500 <= status <= 599:
                retry_after = (
                    self._retry_after_seconds(
                        response.headers.get("Retry-After"),
                    )
                    if status == 429 else None
                )
                backoff = self._backoff(attempt)
                delay = max(retry_after or 0.0, backoff)
                if status == 429:
                    _REQUEST_LIMITER.pause_for(delay)
                if attempt >= MAX_RETRIES:
                    raise TMDBError(
                        f"TMDB is temporarily unavailable ({status}).",
                        self.key,
                    )
                if status != 429:
                    self._wait(delay)
                continue
            if status < 200 or status >= 300:
                raise TMDBError(
                    f"TMDB request failed ({status}).", self.key,
                )
            try:
                data = response.json()
            except requests.exceptions.JSONDecodeError:
                raise TMDBError(
                    "TMDB returned invalid response data.",
                ) from None
            self._check_cancelled()
            return data

    def search(self, query, year=None):
        data = self.get(
            "/search/movie",
            query=query,
            year=year,
            include_adult="false",
        )
        results = (data or {}).get("results", [])
        if not results and year:
            data = self.get(
                "/search/movie", query=query, include_adult="false",
            )
            results = (data or {}).get("results", [])
        return results

    def movie(self, movie_id):
        return self.get(
            f"/movie/{movie_id}", append_to_response="credits",
        )

    def search_tv(self, query, year=None):
        data = self.get(
            "/search/tv",
            query=query,
            first_air_date_year=year,
            include_adult="false",
        )
        results = (data or {}).get("results", [])
        if not results and year:
            data = self.get(
                "/search/tv", query=query, include_adult="false",
            )
            results = (data or {}).get("results", [])
        return results

    def search_tv_strict(self, query, year=None):
        """One search without the retry that drops the year."""
        response = self.get(
            "/search/tv",
            query=query,
            first_air_date_year=year,
            include_adult="false",
        )
        return (response or {}).get("results", [])

    def tv(self, show_id):
        return self.get(
            f"/tv/{show_id}", append_to_response="credits",
        )

    def season(self, show_id, season_number):
        """Fetch a season and all its episodes in one request."""
        return self.get(f"/tv/{show_id}/season/{season_number}")

    def episode(self, show_id, season_number, episode_number):
        return self.get(
            f"/tv/{show_id}/season/{season_number}/episode/{episode_number}",
        )

    def absolute_episode(self, show_id, episode_number):
        """Map an absolute episode number using TMDB's type-2 episode group."""
        response = self.get(f"/tv/{show_id}/episode_groups") or {}
        absolute_group = next(
            (
                group for group in response.get("results", [])
                if group.get("type") == 2
            ),
            None,
        )
        details = (
            self.get(f"/tv/episode_group/{absolute_group['id']}")
            if absolute_group and absolute_group.get("id") else None
        ) or {}
        episodes = []
        groups = sorted(
            details.get("groups") or [],
            key=lambda group: group.get("order", 0),
        )
        for group in groups:
            episodes.extend(sorted(
                group.get("episodes") or [],
                key=lambda episode: episode.get("order", 0),
            ))
        index = episode_number - 1
        if 0 <= index < len(episodes):
            return episodes[index]
        return next(
            (
                episode for episode in episodes
                if episode.get("order") == index
            ),
            None,
        )

    def check(self):
        return self.get("/configuration") is not None
