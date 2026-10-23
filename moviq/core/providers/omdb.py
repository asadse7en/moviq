"""OMDb: free key (1,000 requests/day), movies and series, IMDb ids."""
import re

from .base import Provider, date_or_year, strip_html, year_from

BASE = "https://www.omdbapi.com/"
_MONTHS = {
    m: i for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}


def _native(imdb_id):
    match = re.fullmatch(r"tt(\d+)", str(imdb_id or ""))
    return int(match.group(1)) if match else None


def _release(raw):
    """'22 Jun 2010' -> '2010-06-22'; falls back to the year."""
    match = re.match(r"(\d{1,2}) (\w{3}) (\d{4})", str(raw.get("Released") or ""))
    if match and match.group(2).lower() in _MONTHS:
        return "%s-%02d-%02d" % (
            match.group(3), _MONTHS[match.group(2).lower()], int(match.group(1)),
        )
    return date_or_year("", year_from(raw.get("Year")))


def _poster(raw):
    poster = raw.get("Poster") or ""
    return "" if poster in ("", "N/A") else poster


def _votes(raw):
    digits = re.sub(r"\D", "", str(raw.get("imdbVotes") or ""))
    return float(digits) / 10000 if digits else 0.0


class OMDbProvider(Provider):
    provider_id = "omdb"
    label = "OMDb"
    needs_key = True
    rate = 4.0
    capacity = 6

    def _get(self, params):
        params = dict(params, apikey=self.key)
        data = self.request_json(
            "GET", BASE, params=params, headers={"accept": "application/json"},
        )
        if not isinstance(data, dict):
            return None
        if str(data.get("Response")).lower() == "false":
            message = str(data.get("Error") or "")
            low = message.lower()
            if "invalid api key" in low or "no api key" in low:
                raise self._auth_error()
            if "limit" in low:
                raise self._error("daily request limit reached.")
            return None
        return data

    # ---- search -----------------------------------------------------------
    def _search(self, query, year, kind):
        params = {"s": query, "type": kind, "y": year}
        def load():
            data = self._get({k: v for k, v in params.items() if v})
            rows = (data or {}).get("Search") or []
            out = []
            for row in rows:
                native = _native(row.get("imdbID"))
                if native is None:
                    continue
                date = date_or_year("", year_from(row.get("Year")))
                title = row.get("Title") or ""
                entry = {"id": native, "popularity": 0.0,
                         "poster_path": _poster(row), "overview": ""}
                if kind == "movie":
                    entry.update(title=title, original_title=title,
                                 release_date=date)
                else:
                    entry.update(name=title, original_name=title,
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
    def _details(self, native, kind):
        imdb_id = f"tt{int(native):07d}"
        def load():
            return self._get({"i": imdb_id, "plot": "short"})
        raw = self.cached(f"/{kind}/{imdb_id}", {}, load)
        if not raw or not raw.get("Title"):
            return None
        genres = [
            {"name": g.strip()} for g in str(raw.get("Genre") or "").split(",")
            if g.strip() and g.strip() != "N/A"
        ]
        details = {
            "id": int(native), "overview": strip_html(
                "" if raw.get("Plot") == "N/A" else raw.get("Plot")),
            "poster_path": _poster(raw), "genres": genres,
            "popularity": _votes(raw),
        }
        date = _release(raw)
        if kind == "movie":
            details.update(title=raw["Title"], original_title=raw["Title"],
                           release_date=date)
        else:
            total = raw.get("totalSeasons")
            count = int(total) if str(total).isdigit() else 0
            details.update(
                name=raw["Title"], original_name=raw["Title"],
                first_air_date=date, number_of_seasons=count,
                seasons=[
                    {"season_number": n, "name": f"Season {n}"}
                    for n in range(1, count + 1)
                ],
            )
        return details

    def movie(self, movie_id):
        return self._details(movie_id, "movie")

    def tv(self, show_id):
        return self._details(show_id, "tv")

    def season(self, show_id, season_number):
        imdb_id = f"tt{int(show_id):07d}"
        def load():
            data = self._get({"i": imdb_id, "Season": season_number})
            episodes = []
            for raw in (data or {}).get("Episodes") or []:
                number = raw.get("Episode")
                if not str(number).isdigit():
                    continue
                episodes.append({
                    "id": _native(raw.get("imdbID")),
                    "season_number": season_number,
                    "episode_number": int(number),
                    "name": raw.get("Title") or "",
                    "air_date": date_or_year(raw.get("Released"), None),
                })
            return {"season_number": season_number, "episodes": episodes} if episodes else None
        return self.cached(f"/season/{imdb_id}", {"s": season_number}, load)

    def check(self):
        data = self._get({"i": "tt0111161"})
        return bool(data and data.get("Title"))
