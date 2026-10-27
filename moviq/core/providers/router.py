"""Tries providers in the user's priority order behind the TMDB interface."""
from ..matching import _normalized_match_title
from ..naming import fold_diacritics, similarity
from ..tmdb import (
    TMDB, TMDBAuthError, TMDBCancelled, TMDBError, TMDBNetworkError,
)
from . import ids
from .anilist import AniListProvider
from .offline import OfflineProvider
from .omdb import OMDbProvider
from .settings import GENERAL, PROVIDER_INFO, ProviderSettings
from .tvmaze import TVmazeProvider

ACCEPTABLE_SIMILARITY = 0.75
_CLASSES = {
    "tvmaze": TVmazeProvider, "omdb": OMDbProvider,
    "anilist": AniListProvider, "offline": OfflineProvider,
}


def _title_of(result):
    return (
        result.get("title") or result.get("name")
        or result.get("original_title") or result.get("original_name") or ""
    )


class ProviderRouter:
    """Route metadata requests through a selected source or priority chain."""

    def __init__(
        self, settings=None, tmdb_key="", language=None, cancel_event=None,
        operation_cache=None, session=None,
    ):
        self.settings = settings or ProviderSettings()
        self.key = (tmdb_key or "").strip()
        self.language = language
        self.cancel_event = cancel_event
        self.operation_cache = operation_cache
        self.notices = []
        self._session = session
        self._built = {}
        self._failed = {}

    # ---- providers ----------------------------------------------------
    def _provider(self, name):
        provider = self._built.get(name)
        if provider is None:
            if name == "tmdb":
                provider = TMDB(
                    self.key, self.language, self.cancel_event,
                    self.operation_cache,
                )
            else:
                provider = _CLASSES[name](
                    key=self.settings.key(name), language=self.language,
                    cancel_event=self.cancel_event,
                    operation_cache=self.operation_cache,
                    session=self._session,
                )
            self._built[name] = provider
        return provider

    def _usable(self, name, kind):
        if name == "offline" and name not in self.settings.enabled:
            return False
        if name in self._failed:
            return False
        provider = self._provider(name)
        return kind in provider.kinds and provider.available()

    def chain(self, kind):
        """Provider names to try for ``kind`` ('movie' or 'tv'), in order."""
        if self.settings.selected != "automatic":
            name = self.settings.selected
            return [name] if self._usable(name, kind) else []
        names = [
            name for name in self.settings.order
            if name != "offline" and name in self.settings.enabled
            and self._usable(name, kind)
        ]
        if (
            "offline" in self.settings.enabled
            and "offline" not in self._failed
        ):
            names.append("offline")
        return names

    def available(self, kind=None):
        kinds = (kind,) if kind else ("movie", "tv")
        return any(
            any(name != "offline" for name in self.chain(k))
            for k in kinds
        )

    @property
    def signature(self):
        return (self.settings, self.key, self.language)

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _tag(name, result):
        if not isinstance(result, dict):
            return result
        tagged = dict(result)
        if tagged.get("id") is not None:
            tagged["id"] = ids.encode(name, tagged["id"])
        tagged["provider"] = name
        return tagged

    @staticmethod
    def _acceptable(results, query):
        wanted = fold_diacritics(_normalized_match_title(query))
        if not wanted:
            return True
        for result in results:
            for title in (
                _title_of(result), result.get("original_title"),
                result.get("original_name"),
            ):
                if title and similarity(
                    wanted, _normalized_match_title(title),
                ) >= ACCEPTABLE_SIMILARITY:
                    return True
        return False

    def _note(self, error):
        text = str(error)
        if text not in self.notices:
            self.notices.append(text)

    def _search(self, kind, method, query, year):
        names = self.chain(kind)
        online_names = [name for name in names if name != "offline"]
        offline_enabled = "offline" in names
        weak = None
        first_error = None
        network_unavailable = False
        online_search_succeeded = False
        other_provider_error = False
        for name in online_names:
            provider = self._provider(name)
            try:
                results = getattr(provider, method)(query, year)
            except TMDBCancelled:
                raise
            except TMDBNetworkError as error:
                self._note(error)
                first_error = first_error or error
                network_unavailable = True
                continue
            except TMDBAuthError as error:
                self._failed[name] = error
                self._note(error)
                first_error = first_error or error
                other_provider_error = True
                continue
            except TMDBError as error:
                self._note(error)
                first_error = first_error or error
                other_provider_error = True
                continue
            online_search_succeeded = True
            tagged = [self._tag(name, result) for result in results or []]
            if not tagged:
                continue
            if name == "offline" or self._acceptable(tagged, query):
                return tagged
            weak = weak or tagged
        if weak is not None:
            return weak
        if (
            network_unavailable and not online_search_succeeded
            and not other_provider_error
            and offline_enabled
        ):
            provider = self._provider("offline")
            return [
                self._tag("offline", result)
                for result in getattr(provider, method)(query, year) or []
            ]
        if (
            network_unavailable and online_search_succeeded
            and not other_provider_error
        ):
            return []
        if first_error is not None:
            raise first_error
        if not online_names:
            raise TMDBAuthError(
                "No metadata provider is available. Add an API key or "
                "enable a provider in Settings.",
            )
        return []

    # ---- TMDB-compatible interface -------------------------------------
    def search(self, query, year=None):
        return self._search("movie", "search", query, year)

    def search_tv(self, query, year=None):
        return self._search("tv", "search_tv", query, year)

    def search_tv_strict(self, query, year=None):
        return self._search("tv", "search_tv_strict", query, year)

    def _resolve(self, composite_id):
        name, native = ids.decode(composite_id)
        provider = self._provider(name)
        if not provider.available():
            raise TMDBAuthError(
                f"{PROVIDER_INFO[name]['label']} needs an API key. "
                "Add it in Settings.",
            )
        return name, provider, native

    def movie(self, movie_id):
        name, provider, native = self._resolve(movie_id)
        return self._tag(name, provider.movie(native))

    def tv(self, show_id):
        name, provider, native = self._resolve(show_id)
        return self._tag(name, provider.tv(native))

    def season(self, show_id, season_number):
        _name, provider, native = self._resolve(show_id)
        return provider.season(native, season_number)

    def episode(self, show_id, season_number, episode_number):
        _name, provider, native = self._resolve(show_id)
        return provider.episode(native, season_number, episode_number)

    def absolute_episode(self, show_id, episode_number):
        _name, provider, native = self._resolve(show_id)
        return provider.absolute_episode(native, episode_number)

    def check(self):
        return any(
            self._provider(name).check()
            for name in (
                self.chain("tv") + self.chain("movie")
            ) if name != "offline"
        )

    @staticmethod
    def parse_typed_id(text):
        return ids.parse_typed_id(text)


def build_router(
    config, tmdb_key, language, cancel_event=None, operation_cache=None,
):
    return ProviderRouter(
        ProviderSettings.from_config(config), tmdb_key, language,
        cancel_event, operation_cache,
    )


def test_provider(name, settings, tmdb_key="", session=None):
    """Return a short status for the Settings page: Connected / Rejected / ..."""
    router = ProviderRouter(settings, tmdb_key, session=session)
    provider = router._provider(name)
    if provider.needs_key and not provider.available():
        return "Needs a key"
    try:
        return "Connected" if provider.check() else "Rejected"
    except TMDBAuthError:
        return "Rejected"
    except TMDBError as error:
        return str(error)[:80]
