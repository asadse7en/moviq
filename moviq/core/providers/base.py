"""Shared HTTP behavior for providers: rate limit, retries, cache, cancel."""
import logging
import re
import sqlite3
import threading

import requests

from ..tmdb import (
    REQUEST_TIMEOUT, TMDB, TMDBAuthError, TMDBCancelled, TMDBError,
    TMDBNetworkError, _TokenBucket,
)
from ..tmdb_cache import get_cache

log = logging.getLogger(__name__)
MAX_RETRIES = 3
_SESSION = requests.Session()
_LIMITERS = {}
_LIMITER_LOCK = threading.Lock()
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text):
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


def year_from(value):
    match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else None


def date_or_year(date_text, year):
    """TMDB-style date string; falls back to 'YYYY-01-01' from a bare year."""
    date_text = str(date_text or "")
    if re.match(r"^\d{4}-\d{2}-\d{2}", date_text):
        return date_text[:10]
    year = year or year_from(date_text)
    return f"{year}-01-01" if year else ""


class Provider:
    """Common provider interface (mirrors the TMDB client's methods)."""

    provider_id = ""
    label = ""
    needs_key = False
    kinds = ("movie", "tv")
    rate = 2.0
    capacity = 4

    def __init__(
        self, key="", language=None, cancel_event=None,
        operation_cache=None, pin="", session=None,
    ):
        self.key = (key or "").strip()
        self.pin = (pin or "").strip()
        self.language = language
        self.cancel_event = cancel_event
        self.operation_cache = operation_cache
        self.session = session or _SESSION

    # ---- availability ----------------------------------------------------
    def available(self):
        return bool(self.key) if self.needs_key else True

    # ---- interface used by the matcher (override as needed) ---------------
    def search(self, query, year=None):
        return []

    def movie(self, movie_id):
        return None

    def search_tv(self, query, year=None):
        return []

    def search_tv_strict(self, query, year=None):
        return self.search_tv(query, year)

    def tv(self, show_id):
        return None

    def season(self, show_id, season_number):
        return None

    def episode(self, show_id, season_number, episode_number):
        season = self.season(show_id, season_number) or {}
        for episode in season.get("episodes") or []:
            if episode.get("episode_number") == episode_number:
                return episode
        return None

    def absolute_episode(self, show_id, episode_number):
        return None

    def check(self):
        return True

    # ---- helpers -----------------------------------------------------------
    @property
    def limiter(self):
        with _LIMITER_LOCK:
            limiter = _LIMITERS.get(self.provider_id)
            if limiter is None:
                limiter = _LIMITERS[self.provider_id] = _TokenBucket(
                    self.rate, self.capacity,
                )
            return limiter

    def _check_cancelled(self):
        if self.cancel_event and self.cancel_event.is_set():
            raise TMDBCancelled()

    def _wait(self, seconds):
        if self.cancel_event:
            if self.cancel_event.wait(seconds):
                raise TMDBCancelled()
        else:
            import time
            time.sleep(seconds)

    def _error(self, message):
        return TMDBError(f"{self.label}: {message}", self.key or None)

    def _auth_error(self, message=None):
        return TMDBAuthError(
            f"{self.label}: {message or 'the API key was rejected. Check it in Settings.'}",
            self.key or None,
        )

    def request_json(
        self, method, url, params=None, headers=None, json_body=None,
        auth_statuses=(401, 403),
    ):
        """HTTP with retries. Returns parsed JSON, or None for HTTP 404."""
        for attempt in range(MAX_RETRIES + 1):
            self._check_cancelled()
            self.limiter.acquire(self.cancel_event)
            try:
                response = self.session.request(
                    method, url, params=params, headers=headers,
                    json=json_body, timeout=REQUEST_TIMEOUT,
                )
            except requests.exceptions.Timeout:
                if attempt >= MAX_RETRIES:
                    raise TMDBNetworkError(
                        f"{self.label}: request timed out.",
                        self.key or None,
                    ) from None
                self._wait(TMDB._backoff(attempt))
                continue
            except requests.exceptions.ConnectionError:
                if attempt >= MAX_RETRIES:
                    raise TMDBNetworkError(
                        f"{self.label}: could not connect.",
                        self.key or None,
                    ) from None
                self._wait(TMDB._backoff(attempt))
                continue
            except requests.exceptions.RequestException:
                raise TMDBNetworkError(
                    f"{self.label}: request failed.",
                    self.key or None,
                ) from None
            status = response.status_code
            if status in auth_statuses:
                raise self._auth_error()
            if status == 404:
                return None
            if status == 429 or 500 <= status <= 599:
                retry_after = (
                    TMDB._retry_after_seconds(
                        response.headers.get("Retry-After"),
                    ) if status == 429 else None
                )
                delay = max(retry_after or 0.0, TMDB._backoff(attempt))
                if status == 429:
                    self.limiter.pause_for(delay)
                if attempt >= MAX_RETRIES:
                    raise self._error(
                        f"temporarily unavailable ({status}).",
                    )
                if status != 429:
                    self._wait(delay)
                continue
            if status < 200 or status >= 300:
                raise self._error(f"request failed ({status}).")
            try:
                data = response.json()
            except ValueError:
                raise self._error("returned invalid data.") from None
            self._check_cancelled()
            return data

    def cached(self, path, params, loader):
        """Cache normalized (TMDB-shaped) data in memory and on disk.

        Credentials never appear in ``params`` or ``path``.
        """
        self._check_cancelled()
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_path = f"/{self.provider_id}{path}"

        def load():
            persistent = None
            persistent_key = None
            try:
                persistent = get_cache()
                if persistent is not None:
                    persistent_key = persistent.key(cache_path, params)
                    found, value = persistent.get(persistent_key)
                    if found:
                        return value
            except (OSError, sqlite3.Error) as error:
                log.warning("Could not read response cache: %s", error)
                persistent = None
            data = loader()
            if data and persistent is not None:
                try:
                    persistent.set(persistent_key, cache_path, data)
                except (OSError, sqlite3.Error, TypeError, ValueError) as error:
                    log.warning("Could not write response cache: %s", error)
            return data

        if self.operation_cache is not None:
            key = (
                "provider", self.provider_id, path,
                tuple(sorted((k, str(v)) for k, v in params.items())),
            )
            return self.operation_cache.get_or_load(key, load)
        return load()

    @staticmethod
    def results(items):
        """Wrap a list so the persistent cache can judge completeness."""
        return {"results": items}
