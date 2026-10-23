"""Provider-aware integer IDs.

The rest of the app stores a single integer per match (overrides, caches,
history). To support several providers without touching all of that, every
non-TMDB id is shifted by a per-provider offset. TMDB ids are unchanged, so
existing overrides and caches keep working.
"""
import re

OFFSET = 10 ** 12
PROVIDER_IDS = ("tmdb", "tvmaze", "omdb", "tvdb", "anilist", "offline")
_INDEX = {name: number for number, name in enumerate(PROVIDER_IDS)}
LABELS = {
    "tmdb": "TMDb", "tvmaze": "TVmaze", "omdb": "OMDb (IMDb)",
    "tvdb": "TheTVDB", "anilist": "AniList", "offline": "Offline guess",
}
_TYPED_ID = re.compile(
    r"^(tmdb|tvdb|tvmaze|anilist|omdb|imdb)\s*[:=]\s*(?:tt)?(\d+)$",
    re.IGNORECASE,
)


def encode(provider, native_id):
    number = int(native_id)
    if provider == "tmdb":
        return number
    return _INDEX[provider] * OFFSET + number


def decode(composite_id):
    index, number = divmod(int(composite_id), OFFSET)
    if index >= len(PROVIDER_IDS):
        return "tmdb", int(composite_id)
    return PROVIDER_IDS[index], number


def provider_of(composite_id):
    return decode(composite_id)[0]


def native_id(composite_id):
    return decode(composite_id)[1]


def is_volatile(composite_id):
    """Offline guesses have no real id; they cannot be loaded again later."""
    return provider_of(composite_id) == "offline"


def display_id(composite_id):
    provider, number = decode(composite_id)
    if provider == "omdb":
        return f"IMDb tt{number:07d}"
    return f"{LABELS[provider]} {number}"


def external_tag(composite_id):
    """(label, value) for folder-name tags, or None when there is no standard.

    Jellyfin/Plex understand tmdb, tvdb and imdb ids only.
    """
    provider, number = decode(composite_id)
    if provider == "tmdb":
        return "tmdb", str(number)
    if provider == "tvdb":
        return "tvdb", str(number)
    if provider == "omdb":
        return "imdb", f"tt{number:07d}"
    return None


def parse_typed_id(text):
    """'tvdb:81189', 'imdb:tt0111161' -> composite id; plain digits -> None."""
    match = _TYPED_ID.match((text or "").strip())
    if not match:
        return None
    prefix = match.group(1).lower()
    provider = "omdb" if prefix == "imdb" else prefix
    return encode(provider, int(match.group(2)))
