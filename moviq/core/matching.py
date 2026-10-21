import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .naming import fold_diacritics, similarity, tv_episode_name_for


_BRACKETED_TAG_RE = re.compile(r"[\[\(\{].*?[\]\)\}]")
_YEAR_TAG_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_RELEASE_TAG_RE = re.compile(
    r"\b(?:480p|576p|720p|1080p|1080i|2160p|4k|uhd|"
    r"blu ray|bluray|bdrip|brrip|webrip|web dl|webdl|web|hdrip|"
    r"dvdrip|dvdscr|hdtv|hdcam|cam|hdts|telesync|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|aac|ac3|eac3|dts|"
    r"ddp5|dd5|atmos|10bit|8bit|hdr10|hdr|dolby|remux|"
    r"proper|repack|extended|unrated|remastered|remaster|"
    r"directors cut|director s cut|imax|dual audio|multi|"
    r"hindi|english|esub|esubs|yify|yts|rarbg|ettv|galaxyrg|"
    r"amzn|nf|dsnp)\b",
)
_RELEASE_GROUP_RE = re.compile(r"[-_.][A-Z0-9][A-Z0-9._-]*$")
_GENERIC_TITLE_WORDS = {"a", "an", "film", "movie", "part", "the", "video"}
_PLACEHOLDER_WORD_RE = re.compile(
    r"^(?:title|sample|test|untitled|placeholder|dummy|final|copy|temp|"
    r"new|unknown|clip|file|name|movies|films)\d*$"
)
_SYMBOL_RE = re.compile(r"[@#$%^*+=~|<>\\/]+")
_YEAR_ONLY_RE = re.compile(r"^(?:19|20)\d{2}$")
NO_MATCH_FLOOR = 0.5
MatchGroupKey = Tuple[str, str, Optional[int]]


def normalize_search_title(title: str) -> str:
    """Strip release metadata while preserving the title's display casing."""
    title = _BRACKETED_TAG_RE.sub(" ", title or "")
    title = _SYMBOL_RE.sub(" ", title)
    year_only = " ".join(title.replace(".", " ").replace("_", " ").split())
    title = _YEAR_TAG_RE.sub(" ", title)
    release = _RELEASE_TAG_RE.search(title)
    if release and release.start() > 0:
        title = title[:release.start()]
    else:
        release_group = _RELEASE_GROUP_RE.search(title)
        if release_group and release_group.start() > 0:
            title = title[:release_group.start()]
    title = re.sub(r"[._]+", " ", title)
    cleaned = " ".join(title.split()).strip(" -")
    if not cleaned and _YEAR_ONLY_RE.match(year_only):
        return year_only
    return cleaned


def search_group_key(
    title: str, year: Optional[int], media_type: str,
) -> MatchGroupKey:
    """Build a stable per-title search key without changing output naming."""
    return (
        media_type,
        _normalized_match_title(normalize_search_title(title)),
        year,
    )


def group_match_items(
    items: Iterable[Any],
) -> List[Tuple[MatchGroupKey, List[Any]]]:
    """Group files that can share one movie or series search."""
    groups: Dict[MatchGroupKey, List[Any]] = {}
    for item in items:
        key = search_group_key(
            item.query, item.year, getattr(item, "media_type", "movie"),
        )
        groups.setdefault(key, []).append(item)
    return list(groups.items())


def _normalized_match_title(value):
    original = value
    value = _BRACKETED_TAG_RE.sub(" ", value or "")
    value = fold_diacritics(unicodedata.normalize("NFKC", value).casefold())
    value = value.replace("&", " and ")
    value = re.sub(r"[\W_]+", " ", value)
    value = _YEAR_TAG_RE.sub(" ", value)
    value = _RELEASE_TAG_RE.sub(" ", value)
    return " ".join(value.split()) or " ".join(_title_words(original))


def _title_words(value):
    value = _BRACKETED_TAG_RE.sub(" ", value or "")
    value = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\W_]+", " ", value.replace("&", " and ")).split()


def _is_placeholder_word(word):
    return word in _GENERIC_TITLE_WORDS or bool(
        _PLACEHOLDER_WORD_RE.match(word)
    )


def _is_generic_title(value):
    """True for empty, placeholder-only or too-short titles."""
    words = _title_words(value)
    meaningful = [word for word in words if not _is_placeholder_word(word)]
    if not meaningful:
        return True
    if all(word.isdigit() for word in meaningful):
        # "007" or "300" are real titles; "Movie 202" is a placeholder.
        return len(meaningful) != len(words)
    return sum(
        len(word) * (1 if word.isascii() else 2)
        for word in meaningful if any(char.isalpha() for char in word)
    ) < 3


def is_year_like_title(value):
    """A title that is only a year, e.g. 1917 or 2020, is a weak signal."""
    return bool(_YEAR_ONLY_RE.match(" ".join(_title_words(value))))


def year_of(result):
    date = (result.get("release_date") or "")[:4]
    return int(date) if date.isdigit() else None


def score(result, query):
    title = result.get("title")
    if _is_generic_title(query) or _is_generic_title(title):
        return 0.0
    return similarity(
        _normalized_match_title(query),
        _normalized_match_title(title),
    )


def _year_closeness(result, year):
    result_year = year_of(result)
    if not year or result_year is None:
        return 0
    gap = abs(result_year - year)
    return 2 if gap == 0 else 1 if gap == 1 else 0


def rank(results, query, year=None):
    return sorted(
        results,
        key=lambda result: (
            round(score(result, query), 2),
            _year_closeness(result, year),
            result.get("popularity", 0),
        ),
        reverse=True,
    )


def is_confident(ranked, query, year, threshold=0.9):
    if not ranked or score(ranked[0], query) < threshold:
        return False
    if year:
        result_year = year_of(ranked[0])
        return result_year is not None and abs(result_year - year) <= 1
    return len([result for result in ranked if score(result, query) >= threshold]) == 1


def to_choice(result):
    choice = {
        "id": result["id"],
        "title": result["title"],
        "year": year_of(result),
    }
    if result.get("original_title"):
        choice["original_title"] = result["original_title"]
    if result.get("poster_path"):
        choice["poster_path"] = result["poster_path"]
    return choice


def tv_year_of(result):
    date = (result.get("first_air_date") or "")[:4]
    return int(date) if date.isdigit() else result.get("year")


def tv_score(result, query):
    title = result.get("name") or result.get("title")
    return similarity(
        _normalized_match_title(query),
        _normalized_match_title(title),
    )


def rank_tv(results, query):
    return sorted(
        results,
        key=lambda result: (
            tv_score(result, query), result.get("popularity", 0),
        ),
        reverse=True,
    )


def is_confident_tv(ranked, query, year, threshold=0.9):
    if not ranked or tv_score(ranked[0], query) < threshold:
        return False
    if year:
        result_year = tv_year_of(ranked[0])
        return result_year is not None and abs(result_year - year) <= 1
    return len([
        result for result in ranked
        if tv_score(result, query) >= threshold
    ]) == 1


def tv_episode_candidates(ranked, query, year, threshold=0.9):
    """Return show candidates supported by the filename title and year."""
    candidates = [
        result for result in ranked
        if tv_score(result, query) >= threshold
    ]
    if year:
        candidates = [
            result for result in candidates
            if (
                tv_year_of(result) is not None
                and abs(tv_year_of(result) - year) <= 1
            )
        ]
    return candidates


def match_tv_episode_title(
    candidates, season_number, episode_number, filename_title, episodes,
    threshold=0.9,
):
    """Resolve a show title ambiguity only when its episode title is unique."""
    if not filename_title:
        return None
    matches = []
    for result in candidates:
        episode = episodes.get(result.get("id"))
        if not episode:
            continue
        if (
            episode.get("season_number") != season_number
            or episode.get("episode_number") != episode_number
        ):
            continue
        if similarity(filename_title, episode.get("name")) >= threshold:
            matches.append((result, episode))
    return matches[0] if len(matches) == 1 else None


def to_tv_choice(
    result, season_number, episode_number, episode_title=None,
    episode_end_number=None,
):
    choice = {
        "id": result["id"],
        "title": result.get("name") or result.get("original_name"),
        "year": tv_year_of(result),
        "media_type": "tv",
        "season_number": season_number,
        "episode_number": episode_number,
        "episode_end_number": episode_end_number,
        "episode_title": episode_title,
    }
    if result.get("original_name"):
        choice["original_name"] = result["original_name"]
    if result.get("poster_path"):
        choice["poster_path"] = result["poster_path"]
    return choice


def tv_match_is_applicable(status, choice):
    """Only confirmed shows with complete episode data can be renamed."""
    if status not in ("auto", "manual", "no_change"):
        return False
    try:
        tv_episode_name_for(choice)
    except (KeyError, TypeError, ValueError):
        return False
    return True
