"""AniList: free GraphQL API for anime, no key (about 30-90 requests/minute).

AniList lists every season/cour as its own entry and has no episode titles,
so episodes are named "Episode N" unless AniList knows the streaming titles.
"""
import re

from .base import Provider, date_or_year, strip_html

URL = "https://graphql.anilist.co"
_TV_FORMATS = "[TV, TV_SHORT, OVA, ONA, SPECIAL]"
_MEDIA_FIELDS = """
  id
  title { romaji english native }
  startDate { year month day }
  format episodes popularity genres
  coverImage { large }
  description(asHtml: false)
  nextAiringEpisode { episode }
  streamingEpisodes { title }
"""
_STREAM_TITLE = re.compile(r"^Episode\s+(\d+)\s*[-\u2013:]\s*(.+)$", re.I)


def _names(raw):
    title = raw.get("title") or {}
    romaji = title.get("romaji") or ""
    english = title.get("english") or ""
    native = title.get("native") or ""
    name = english or romaji or native
    original = romaji if romaji and romaji != name else native
    return name, original or name


def _date(raw):
    start = raw.get("startDate") or {}
    year, month, day = start.get("year"), start.get("month"), start.get("day")
    if year and month and day:
        return "%04d-%02d-%02d" % (year, month, day)
    return date_or_year("", year)


class AniListProvider(Provider):
    provider_id = "anilist"
    label = "AniList"
    needs_key = False
    rate = 0.4          # stays under AniList's reduced 30/min limit
    capacity = 3

    def _query(self, query, variables):
        data = self.request_json(
            "POST", URL, headers={"accept": "application/json"},
            json_body={"query": query, "variables": variables},
        )
        return (data or {}).get("data") if isinstance(data, dict) else None

    def _search(self, query, formats):
        def load():
            gql = (
                "query ($search: String) { Page(page: 1, perPage: 10) { "
                f"media(search: $search, type: ANIME, format_in: {formats}, "
                f"sort: SEARCH_MATCH) {{ {_MEDIA_FIELDS} }} }} }}"
            )
            data = self._query(gql, {"search": query}) or {}
            return self.results(
                (data.get("Page") or {}).get("media") or [],
            )
        return list((self.cached("/search", {"q": query, "f": formats}, load) or {}).get("results") or [])

    def _shape(self, raw, kind):
        name, original = _names(raw)
        base = {
            "id": raw["id"], "popularity": float(raw.get("popularity") or 0) / 1000,
            "poster_path": (raw.get("coverImage") or {}).get("large") or "",
            "overview": strip_html(raw.get("description")),
            "genres": [{"name": g} for g in raw.get("genres") or []],
        }
        if kind == "movie":
            base.update(title=name, original_title=original,
                        release_date=_date(raw))
        else:
            base.update(name=name, original_name=original,
                        first_air_date=_date(raw))
        return base

    def _results(self, query, year, formats, kind, strict):
        rows = [
            self._shape(raw, kind) for raw in self._search(query, formats)
            if raw.get("id")
        ]
        if year:
            same = [r for r in rows if str(r.get("release_date") or r.get("first_air_date"))[:4] == str(year)]
            if same or strict:
                return same
        return rows

    def search(self, query, year=None):
        return self._results(query, year, "[MOVIE]", "movie", False)

    def search_tv_strict(self, query, year=None):
        return self._results(query, year, _TV_FORMATS, "tv", True)

    def search_tv(self, query, year=None):
        return self._results(query, year, _TV_FORMATS, "tv", False)

    def _media(self, media_id):
        def load():
            gql = (
                "query ($id: Int) { Media(id: $id, type: ANIME) { "
                f"{_MEDIA_FIELDS} }} }}"
            )
            data = self._query(gql, {"id": int(media_id)}) or {}
            return data.get("Media")
        return self.cached(f"/media/{int(media_id)}", {}, load)

    def movie(self, movie_id):
        raw = self._media(movie_id)
        return self._shape(raw, "movie") if raw else None

    @staticmethod
    def _episode_count(raw):
        if raw.get("episodes"):
            return int(raw["episodes"])
        upcoming = (raw.get("nextAiringEpisode") or {}).get("episode")
        if upcoming:
            return max(int(upcoming) - 1, 0)
        return len(raw.get("streamingEpisodes") or [])

    def tv(self, show_id):
        raw = self._media(show_id)
        if not raw:
            return None
        details = self._shape(raw, "tv")
        count = self._episode_count(raw)
        details.update(
            number_of_seasons=1,
            seasons=[{
                "season_number": 1, "episode_count": count, "name": "Season 1",
            }],
        )
        return details

    def season(self, show_id, season_number):
        if season_number != 1:
            return None
        raw = self._media(show_id)
        if not raw:
            return None
        titles = {}
        for entry in raw.get("streamingEpisodes") or []:
            match = _STREAM_TITLE.match(entry.get("title") or "")
            if match:
                titles[int(match.group(1))] = match.group(2).strip()
        count = self._episode_count(raw)
        return {"season_number": 1, "episodes": [
            {
                "id": None, "season_number": 1, "episode_number": number,
                "name": titles.get(number, f"Episode {number}"),
                "air_date": "",
            }
            for number in range(1, count + 1)
        ]}

    def absolute_episode(self, show_id, episode_number):
        season = self.season(show_id, 1) or {}
        for episode in season.get("episodes") or []:
            if episode["episode_number"] == episode_number:
                return episode
        return None

    def check(self):
        return self._query("query { SiteStatistics { anime { nodes { count } } } }", {}) is not None
