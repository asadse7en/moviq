"""Offline fallback: builds a match from the file name alone.

No network and no key. Every result is sent to Review because nothing was
verified. Ids are not stable, so they are never remembered across sessions.
"""
import threading
import zlib

from .base import Provider, date_or_year

_REGISTRY = {}
_LOCK = threading.Lock()
MAX_EPISODES = 200


def _pretty(title):
    title = " ".join((title or "").split())
    return title.title() if title and title == title.lower() else title


def _id(kind, title, year):
    seed = f"{kind}:{title.casefold()}:{year or ''}".encode("utf-8")
    return zlib.crc32(seed) % 900_000_000 + 1


class OfflineProvider(Provider):
    provider_id = "offline"
    label = "Offline Guess"
    needs_key = False

    def _make(self, query, year, kind):
        title = _pretty(query)
        if not title:
            return []
        entry = {
            "id": _id(kind, title, year), "popularity": 0.0,
            "poster_path": "", "overview": "", "offline": True,
        }
        date = date_or_year("", year)
        if kind == "movie":
            entry.update(title=title, original_title=title, release_date=date)
        else:
            entry.update(name=title, original_name=title, first_air_date=date)
        with _LOCK:
            _REGISTRY[entry["id"]] = dict(entry)
        return [entry]

    def search(self, query, year=None):
        return self._make(query, year, "movie")

    def search_tv(self, query, year=None):
        return self._make(query, year, "tv")

    def movie(self, movie_id):
        with _LOCK:
            return dict(_REGISTRY[movie_id]) if movie_id in _REGISTRY else None

    def tv(self, show_id):
        with _LOCK:
            entry = _REGISTRY.get(show_id)
        if not entry:
            return None
        details = dict(entry)
        details.update(
            number_of_seasons=1,
            seasons=[{"season_number": 1, "episode_count": MAX_EPISODES}],
        )
        return details

    def season(self, show_id, season_number):
        if show_id not in _REGISTRY:
            return None
        return {"season_number": season_number, "episodes": [
            {
                "id": None, "season_number": season_number,
                "episode_number": number, "name": f"Episode {number}",
                "air_date": "",
            }
            for number in range(1, MAX_EPISODES + 1)
        ]}

    def absolute_episode(self, show_id, episode_number):
        return self.season(show_id, 1) and {
            "season_number": 1, "episode_number": episode_number,
            "name": f"Episode {episode_number}", "id": None, "air_date": "",
        }
