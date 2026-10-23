"""TVmaze: free, no key, TV only. Data is CC BY-SA (credit: tvmaze.com)."""
from .base import Provider, date_or_year, strip_html, year_from

BASE = "https://api.tvmaze.com"


def _image(show):
    image = show.get("image") or {}
    return image.get("medium") or image.get("original") or ""


def _show_result(show):
    year = year_from(show.get("premiered"))
    rating = (show.get("rating") or {}).get("average") or 0
    return {
        "id": show["id"],
        "name": show.get("name") or "",
        "original_name": show.get("name") or "",
        "first_air_date": date_or_year(show.get("premiered"), year),
        "popularity": float(show.get("weight") or rating * 10 or 0),
        "poster_path": _image(show),
        "overview": strip_html(show.get("summary")),
    }


class TVmazeProvider(Provider):
    provider_id = "tvmaze"
    label = "TVmaze"
    needs_key = False
    kinds = ("tv",)
    rate = 2.0       # TVmaze allows at least 20 calls per 10 seconds
    capacity = 8

    def _get(self, path, params=None):
        return self.request_json(
            "GET", BASE + path, params=params,
            headers={"accept": "application/json", "user-agent": "Moviq"},
        )

    def search_tv_strict(self, query, year=None):
        def load():
            data = self._get("/search/shows", {"q": query}) or []
            return self.results([
                _show_result(row["show"]) for row in data
                if isinstance(row, dict) and row.get("show")
            ])
        found = self.cached("/search/shows", {"q": query}, load) or {}
        results = list(found.get("results") or [])
        if year:
            results = [
                result for result in results
                if year_from(result.get("first_air_date")) == year
            ]
        return results

    def search_tv(self, query, year=None):
        results = self.search_tv_strict(query, year)
        if not results and year:
            results = self.search_tv_strict(query, None)
        return results

    # ---- episodes ---------------------------------------------------------
    def _episodes(self, show_id):
        def load():
            data = self._get(
                f"/shows/{show_id}/episodes", {"specials": 1},
            )
            return {"episodes": data} if data else None
        found = self.cached(f"/shows/{show_id}/episodes", {"specials": 1}, load)
        return list((found or {}).get("episodes") or [])

    @staticmethod
    def _split(episodes):
        regular = [
            e for e in episodes
            if e.get("type", "regular") == "regular"
            and isinstance(e.get("number"), int)
        ]
        specials = [e for e in episodes if e not in regular]
        return regular, specials

    @staticmethod
    def _episode(raw, season_number, episode_number):
        return {
            "id": raw.get("id"),
            "season_number": season_number,
            "episode_number": episode_number,
            "name": raw.get("name") or "",
            "air_date": raw.get("airdate") or "",
            "overview": strip_html(raw.get("summary")),
            "runtime": raw.get("runtime"),
        }

    def tv(self, show_id):
        def load():
            return self._get(f"/shows/{show_id}")
        show = self.cached(f"/shows/{show_id}", {}, load)
        if not show or not show.get("id"):
            return None
        regular, specials = self._split(self._episodes(show_id))
        counts = {}
        for raw in regular:
            counts[raw["season"]] = counts.get(raw["season"], 0) + 1
        seasons = [
            {
                "season_number": number, "episode_count": count,
                "name": f"Season {number}",
            }
            for number, count in sorted(counts.items())
        ]
        if specials:
            seasons.insert(0, {
                "season_number": 0, "episode_count": len(specials),
                "name": "Specials",
            })
        details = _show_result(show)
        details.update({
            "genres": [{"name": name} for name in show.get("genres") or []],
            "number_of_seasons": len(counts),
            "seasons": seasons,
        })
        return details

    def season(self, show_id, season_number):
        regular, specials = self._split(self._episodes(show_id))
        if season_number == 0:
            specials = sorted(
                specials, key=lambda e: (e.get("airdate") or "", e.get("id") or 0),
            )
            episodes = [
                self._episode(raw, 0, index)
                for index, raw in enumerate(specials, 1)
            ]
        else:
            episodes = [
                self._episode(raw, season_number, raw["number"])
                for raw in regular if raw.get("season") == season_number
            ]
        if not episodes:
            return None
        return {"season_number": season_number, "episodes": episodes}

    def absolute_episode(self, show_id, episode_number):
        regular, _specials = self._split(self._episodes(show_id))
        regular.sort(key=lambda e: (e.get("season") or 0, e.get("number") or 0))
        index = episode_number - 1
        if 0 <= index < len(regular):
            raw = regular[index]
            return self._episode(raw, raw["season"], raw["number"])
        return None

    def check(self):
        return bool(self._get("/shows/1"))
