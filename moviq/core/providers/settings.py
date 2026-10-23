"""User choices for providers: priority order, on/off, and extra keys."""
from dataclasses import dataclass

DEFAULT_ORDER = ("tmdb", "tvmaze", "anilist", "omdb", "tvdb", "offline")
DEFAULT_ENABLED = ("tmdb", "tvmaze", "anilist", "omdb", "tvdb")
# Providers that work for ordinary movies/shows; AniList is anime-only.
GENERAL = ("tmdb", "omdb", "tvdb", "tvmaze")

PROVIDER_INFO = {
    "tmdb": {
        "label": "TMDb", "needs_key": True, "kinds": "Movies and TV",
        "note": "Best overall. Free key from themoviedb.org.",
        "key_url": "https://www.themoviedb.org/settings/api",
    },
    "tvmaze": {
        "label": "TVmaze", "needs_key": False, "kinds": "TV only",
        "note": "No key needed. Data by tvmaze.com (CC BY-SA).",
        "key_url": "https://www.tvmaze.com/api",
    },
    "anilist": {
        "label": "AniList", "needs_key": False, "kinds": "Anime",
        "note": "No key needed. No episode titles.",
        "key_url": "https://anilist.co",
    },
    "omdb": {
        "label": "OMDb (IMDb)", "needs_key": True, "kinds": "Movies and TV",
        "note": "Free key, 1,000 requests a day.",
        "key_url": "https://www.omdbapi.com/apikey.aspx",
    },
    "tvdb": {
        "label": "TheTVDB", "needs_key": True, "kinds": "Movies and TV",
        "note": "Your own API key (and PIN if you have one).",
        "key_url": "https://thetvdb.com/api-information",
    },
    "offline": {
        "label": "Offline guess", "needs_key": False, "kinds": "Movies and TV",
        "note": "From the file name only. Always sent to Review.",
        "key_url": "",
    },
}


def _clean_order(order):
    seen = []
    for name in order or ():
        if name in PROVIDER_INFO and name not in seen:
            seen.append(name)
    for name in DEFAULT_ORDER:
        if name not in seen:
            seen.append(name)
    return tuple(seen)


@dataclass(frozen=True)
class ProviderSettings:
    order: tuple = DEFAULT_ORDER
    enabled: tuple = DEFAULT_ENABLED
    keys: tuple = ()          # ((name, value), ...) for omdb, tvdb, tvdb_pin
    selected: str = "automatic"

    @classmethod
    def from_config(cls, config):
        config = config or {}
        raw = config.get("providers")
        raw = raw if isinstance(raw, dict) else {}
        order = _clean_order(raw.get("order"))
        enabled_raw = raw.get("enabled")
        if isinstance(enabled_raw, dict):
            enabled = tuple(
                name for name in order
                if enabled_raw.get(name, name in DEFAULT_ENABLED) is True
            )
        else:
            enabled = DEFAULT_ENABLED
        keys_raw = config.get("provider_keys")
        keys_raw = keys_raw if isinstance(keys_raw, dict) else {}
        keys = tuple(sorted(
            (name, str(keys_raw.get(name) or "").strip())
            for name in ("omdb", "tvdb", "tvdb_pin")
        ))
        selected = raw.get("selected", "automatic")
        if not isinstance(selected, str) or selected not in PROVIDER_INFO:
            selected = "automatic"
        return cls(order, enabled, keys, selected)

    def key(self, name):
        return dict(self.keys).get(name, "")

    def to_config(self):
        return {
            "providers": {
                "order": list(self.order),
                "enabled": {name: name in self.enabled for name in self.order},
                "selected": self.selected,
            },
            "provider_keys": dict(self.keys),
        }

    def with_order(self, order):
        return ProviderSettings(
            _clean_order(order), self.enabled, self.keys, self.selected,
        )

    def with_enabled(self, name, value):
        enabled = [n for n in self.enabled if n != name]
        if value:
            enabled.append(name)
        return ProviderSettings(
            self.order, tuple(enabled), self.keys, self.selected,
        )

    def with_key(self, name, value):
        keys = dict(self.keys)
        keys[name] = (value or "").strip()
        return ProviderSettings(
            self.order, self.enabled, tuple(sorted(keys.items())),
            self.selected,
        )

    def with_selected(self, name):
        selected = (
            name if isinstance(name, str) and name in PROVIDER_INFO
            else "automatic"
        )
        return ProviderSettings(
            self.order, self.enabled, self.keys, selected,
        )
