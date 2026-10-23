"""TheTVDB v4. You bring your own API key (and subscriber PIN, if it has one).

TheTVDB's free "subscriber-supported" keys are meant for each end user to
register their own key, so Moviq never ships one.
"""
import threading
import time

from ..tmdb import TMDBAuthError
from .base import Provider, date_or_year, strip_html, year_from

BASE = "https://api4.thetvdb.com/v4"
TOKEN_LIFETIME = 20 * 24 * 3600     # tokens last about a month
_TOKENS = {}
_TOKEN_LOCK = threading.Lock()


def _image(value):
    return value if isinstance(value, str) and value.startswith("http") else ""


def _genres(raw):
    return [
        {"name": g["name"]} for g in raw.get("genres") or []
        if isinstance(g, dict) and g.get("name")
    ]


class TVDBProvider(Provider):
    provider_id = "tvdb"
    label = "TheTVDB"
    needs_key = True
    rate = 4.0
    capacity = 6

    # ---- auth ---------------------------------------------------------
    def _token(self, force=False):
        cache_key = (self.key, self.pin)
        with _TOKEN_LOCK:
            token, expires = _TOKENS.get(cache_key, (None, 0))
            if token and expires > time.time() and not force:
                return token
        body = {"apikey": self.key}
        if self.pin:
            body["pin"] = self.pin
        data = self.request_json("POST", f"{BASE}/login", json_body=body)
        token = ((data or {}).get("data") or {}).get("token")
        if not token:
            raise self._auth_error()
        with _TOKEN_LOCK:
            _TOKENS[cache_key] = (token, time.time() + TOKEN_LIFETIME)
        return token

    def _get(self, path, params=None):
        for attempt in (0, 1):
            headers = {
                "accept": "application/json",
                "Authorization": f"Bearer {self._token(force=attempt == 1)}",
            }
            try:
                data = self.request_json(
                    "GET", BASE + path, params=params, headers=headers,
                )
            except TMDBAuthError:
                if attempt == 1:
                    raise
                continue
            return (data or {}).get("data") if isinstance(data, dict) else None
        return None

    def _page_all(self, path):
        """Collect every episode page (the API returns up to ~500 each)."""
        episodes = []
        for page in range(0, 20):
            token = self._token()
            data = self.request_json(
                "GET", BASE + path, params={"page": page},
                headers={
                    "accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )
            if not isinstance(data, dict):
                break
            episodes.extend((data.get("data") or {}).get("episodes") or [])
            if (data.get("links") or {}).get("next") in (None, ""):
                break
        return episodes

    # ---- search -----------------------------------------------------------
    def _search(self, query, year, kind):
        params = {"query": query, "type": kind, "year": year}
        def load():
            rows = self._get("/search", {k: v for k, v in params.items() if v}) or []
            out = []
            for row in rows:
                tvdb_id = str(row.get("tvdb_id") or "").strip()
                if not tvdb_id.isdigit():
                    object_id = str(row.get("objectID") or "")
                    tvdb_id = object_id.split("-")[-1]
                if not tvdb_id.isdigit():
                    continue
                name = row.get("name") or ""
                date = date_or_year(
                    row.get("first_air_time"), year_from(row.get("year")),
                )
                entry = {
                    "id": int(tvdb_id), "popularity": 0.0,
                    "poster_path": _image(row.get("image_url")),
                    "overview": strip_html(row.get("overview")),
                }
                if kind == "movie":
                    entry.update(title=name, original_title=name,
                                 release_date=date)
                else:
                    entry.update(name=name, original_name=name,
                                 first_air_date=date)
                out.append(entry)
            return self.results(out)
        found = self.cached(f"/search/{kind}", params, load) or {}
        return list(found.get("results") or [])

    def search(self, query, year=None):
        results = self._search(query, year, "movie")
        if not results and year:
            results = self._search(query, None, "movie")
        return results

    def search_tv_strict(self, query, year=None):
        return self._search(query, year, "series")

    def search_tv(self, query, year=None):
        results = self.search_tv_strict(query, year)
        if not results and year:
            results = self.search_tv_strict(query, None)
        return results

    # ---- details ----------------------------------------------------------
    def movie(self, movie_id):
        def load():
            return self._get(f"/movies/{int(movie_id)}/extended", {"short": "true"})
        raw = self.cached(f"/movies/{int(movie_id)}", {}, load)
        if not raw or not raw.get("name"):
            return None
        release = (raw.get("first_release") or {}).get("date")
        if not release:
            releases = raw.get("releases") or []
            release = (releases[0] or {}).get("date") if releases else ""
        return {
            "id": int(movie_id), "title": raw["name"],
            "original_title": raw["name"],
            "release_date": date_or_year(release, year_from(raw.get("year"))),
            "overview": strip_html(raw.get("overview")),
            "poster_path": _image(raw.get("image")),
            "genres": _genres(raw), "popularity": 0.0,
        }

    def _episodes(self, show_id):
        def load():
            episodes = self._page_all(f"/series/{int(show_id)}/episodes/default")
            return {"episodes": episodes} if episodes else None
        found = self.cached(f"/series/{int(show_id)}/episodes", {}, load)
        return list((found or {}).get("episodes") or [])

    def tv(self, show_id):
        def load():
            return self._get(f"/series/{int(show_id)}/extended", {"short": "true"})
        raw = self.cached(f"/series/{int(show_id)}", {}, load)
        if not raw or not raw.get("name"):
            return None
        counts = {}
        for episode in self._episodes(show_id):
            number = episode.get("seasonNumber")
            if isinstance(number, int) and number >= 0:
                counts[number] = counts.get(number, 0) + 1
        return {
            "id": int(show_id), "name": raw["name"],
            "original_name": raw.get("originalName") or raw["name"],
            "first_air_date": date_or_year(
                raw.get("firstAired"), year_from(raw.get("year")),
            ),
            "overview": strip_html(raw.get("overview")),
            "poster_path": _image(raw.get("image")),
            "genres": _genres(raw), "popularity": 0.0,
            "number_of_seasons": len([n for n in counts if n >= 1]),
            "seasons": [
                {"season_number": n, "episode_count": c,
                 "name": "Specials" if n == 0 else f"Season {n}"}
                for n, c in sorted(counts.items())
            ],
        }

    @staticmethod
    def _episode(raw):
        return {
            "id": raw.get("id"),
            "season_number": raw.get("seasonNumber"),
            "episode_number": raw.get("number"),
            "name": raw.get("name") or "",
            "air_date": raw.get("aired") or "",
            "overview": strip_html(raw.get("overview")),
            "runtime": raw.get("runtime"),
        }

    def season(self, show_id, season_number):
        episodes = [
            self._episode(raw) for raw in self._episodes(show_id)
            if raw.get("seasonNumber") == season_number
            and isinstance(raw.get("number"), int)
        ]
        episodes.sort(key=lambda e: e["episode_number"])
        if not episodes:
            return None
        return {"season_number": season_number, "episodes": episodes}

    def absolute_episode(self, show_id, episode_number):
        for raw in self._episodes(show_id):
            if raw.get("absoluteNumber") == episode_number and raw.get("seasonNumber"):
                return self._episode(raw)
        return None

    def check(self):
        self._token(force=True)
        return True
