"""User choices for providers: priority order, on/off, and extra keys."""
from dataclasses import dataclass

DEFAULT_ORDER = ("tmdb", "tvmaze", "anilist", "omdb", "offline")
DEFAULT_ENABLED = ("tmdb", "tvmaze", "anilist", "omdb")
# Providers that work for ordinary movies/shows; AniList is anime-only.
GENERAL = ("tmdb", "omdb", "tvmaze")
SOURCE_SELECTIONS = ("automatic", "tmdb", "omdb", "tvmaze", "anilist")

PROVIDER_INFO = {
    "tmdb": {
        "label": "TMDb", "needs_key": True, "kinds": "Movies and TV",
        "note": "Best overall. Free key from themoviedb.org.",
        "description": (
            "Create a free account on TMDB \u2192 Settings \u2192 API "
            "\u2192 paste it here."
        ),
        "description_links": (
            {"text": "TMDB", "url": "https://www.themoviedb.org"},
            {
                "text": "API",
                "url": "https://www.themoviedb.org/settings/api",
            },
        ),
        "key_url": "https://www.themoviedb.org/settings/api",
    },
    "tvmaze": {
        "label": "TVmaze", "needs_key": False, "kinds": "TV only",
        "note": "No key needed. Data by tvmaze.com (CC BY-SA).",
        "description": "Free TV show and episode database. No key needed.",
        "key_url": "https://www.tvmaze.com/api",
    },
    "omdb": {
        "label": "OMDb", "needs_key": True, "kinds": "Movies and TV",
        "note": "Free key, 1,000 requests a day.",
        "description": (
            "Request a free API key from OMDb \u2192 check your email "
            "\u2192 paste it here."
        ),
        "description_link": {
            "text": "OMDb", "url": "https://www.omdbapi.com/apikey.aspx",
        },
        "key_url": "https://www.omdbapi.com/apikey.aspx",
    },
    "anilist": {
        "label": "AniList", "needs_key": False, "kinds": "Anime",
        "note": "No key needed. No episode titles.",
        "description": "Free anime and manga database. No key needed.",
        "key_url": "https://anilist.co",
    },
    "offline": {
        "label": "Offline Guess", "needs_key": False, "kinds": "Movies and TV",
        "note": "From the file name only. Always sent to Review.",
        "description": (
            "Guess from the file name when nothing else can help. "
            "Always sent to Review."
        ),
        "key_url": "",
    },
}


def provider_source_label(selected):
    if selected == "automatic":
        return "Automatic"
    return PROVIDER_INFO.get(selected, {}).get("label", selected)


def _clean_order(order):
    seen = []
    for name in order or ():
        if name in PROVIDER_INFO and name != "offline" and name not in seen:
            seen.append(name)
    for name in DEFAULT_ORDER:
        if name != "offline" and name not in seen:
            seen.append(name)
    seen.append("offline")
    return tuple(seen)


@dataclass(frozen=True)
class ProviderSettings:
    order: tuple = DEFAULT_ORDER
    enabled: tuple = DEFAULT_ENABLED
    keys: tuple = ()          # ((name, value), ...) for omdb
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
            for name in ("omdb",)
        ))
        selected = raw.get("selected", "automatic")
        if not isinstance(selected, str) or selected not in SOURCE_SELECTIONS:
            selected = "automatic"
        return cls(order, enabled, keys, selected)

    def key(self, name):
        return dict(self.keys).get(name, "")

    def can_select(self, name, tmdb_key=""):
        if name == "automatic":
            return True
        if name not in SOURCE_SELECTIONS or name not in self.enabled:
            return False
        if not PROVIDER_INFO[name]["needs_key"]:
            return True
        key = tmdb_key if name == "tmdb" else self.key(name)
        return bool(key.strip())

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
            name if isinstance(name, str) and name in SOURCE_SELECTIONS
            else "automatic"
        )
        return ProviderSettings(
            self.order, self.enabled, self.keys, selected,
        )
