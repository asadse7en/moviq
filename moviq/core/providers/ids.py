"""Provider-aware integer IDs.

The rest of the app stores a single integer per match (overrides, caches,
history). To support several providers without touching all of that, every
non-TMDB id is shifted by a per-provider offset. TMDB ids are unchanged, so
existing overrides and caches keep working.
"""
import re

OFFSET = 10 ** 12
PROVIDER_IDS = ("tmdb", "tvmaze", "omdb", None, "anilist", "offline")
# Index 3 was TheTVDB.
_INDEX = {
    name: number for number, name in enumerate(PROVIDER_IDS) if name is not None
}
LABELS = {
    "tmdb": "TMDb", "tvmaze": "TVmaze", "omdb": "OMDb",
    "anilist": "AniList", "offline": "Offline Guess",
}
_TYPED_ID = re.compile(
    r"^(tmdb|tvmaze|anilist|omdb|imdb)\s*[:=]\s*(?:tt)?(\d+)$",
    re.IGNORECASE,
)


def encode(provider, native_id):
    number = int(native_id)
    if provider == "tmdb":
        return number
    return _INDEX[provider] * OFFSET + number


def decode(composite_id):
    index, number = divmod(int(composite_id), OFFSET)
    if index >= len(PROVIDER_IDS) or PROVIDER_IDS[index] is None:
        return "tmdb", int(composite_id)
    return PROVIDER_IDS[index], number


def provider_of(composite_id):
    return decode(composite_id)[0]


def native_id(composite_id):
    return decode(composite_id)[1]


def is_volatile(composite_id):
    """Offline Guess results have no real id; they cannot be loaded later."""
    return provider_of(composite_id) == "offline"


def display_id(composite_id):
    provider, number = decode(composite_id)
    if provider == "omdb":
        return f"IMDb tt{number:07d}"
    return f"{LABELS[provider]} {number}"


def external_tag(composite_id):
    """(label, value) for folder-name tags, or None when there is no standard.

    Jellyfin/Plex understand tmdb and imdb ids only.
    """
    provider, number = decode(composite_id)
    if provider == "tmdb":
        return "tmdb", str(number)
    if provider == "omdb":
        return "imdb", f"tt{number:07d}"
    return None


def parse_typed_id(text):
    """'imdb:tt0111161' -> composite id; plain digits -> None."""
    match = _TYPED_ID.match((text or "").strip())
    if not match:
        return None
    prefix = match.group(1).lower()
    provider = "omdb" if prefix == "imdb" else prefix
    return encode(provider, int(match.group(2)))
