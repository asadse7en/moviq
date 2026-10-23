"""Metadata providers (TMDB, TVmaze, OMDb, TheTVDB, AniList, offline).

Every provider returns TMDB-shaped dictionaries so the matching code stays
provider-agnostic. ``router.ProviderRouter`` tries the enabled providers in
the user's priority order. Import ``router`` and ``settings`` directly; this
package only re-exports the lightweight id helpers (avoids import cycles).
"""
from .ids import (
    decode, display_id, encode, external_tag, is_volatile, native_id,
    parse_typed_id, provider_of,
)

__all__ = [
    "decode", "display_id", "encode", "external_tag", "is_volatile",
    "native_id", "parse_typed_id", "provider_of",
]
