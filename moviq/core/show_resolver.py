from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
import json
from pathlib import Path
import re
import threading
from typing import Optional

from .config import SHOW_OVERRIDES_FILE, migrate_user_data
from .naming import fold_diacritics, parse_tv_season_hint
from .providers.ids import is_volatile, provider_of
from .tmdb import TMDB, TMDBError

try:  # DEBUG-TRACE
    import debug_trace as dbg  # DEBUG-TRACE
except Exception:  # DEBUG-TRACE
    dbg = None  # DEBUG-TRACE


class ShowState(str, Enum):
    PENDING = "PENDING"
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ShowResult:
    state: ShowState
    tmdb_id: Optional[int] = None
    name: Optional[str] = None
    year: Optional[int] = None
    confidence: float = 0.0
    error: Optional[str] = None
    candidate: Optional[dict] = None
    ambiguous: bool = False


_GENERIC_WORDS = {"the", "show", "name", "example", "episode"}
_COUNTRY_SUFFIX_RE = re.compile(r"(?i)\s+(?:US|UK|AU)$")
_LEADING_ARTICLE_RE = re.compile(r"(?i)^the\s+")
_EMBEDDED_YEAR_RE = re.compile(
    r"(?:\s*\(((?:19|20)\d{2})\)|[ ._-]+((?:19|20)\d{2}))\s*$",
)


def normalize_show_title(title):
    title, _hint = parse_tv_season_hint(title or "")
    numeric_sequel = re.search(r"(?<!\d)\s+(\d{1,3})$", title)
    if numeric_sequel and not 1900 <= int(numeric_sequel.group(1)) <= 2100:
        title = title[:numeric_sequel.start()].strip()
    return " ".join(re.findall(r"\w+", title.casefold()))


def normalize_show_key(title, year=None):
    return normalize_show_title(title), year


def _similarity(left, right):
    left_words = set(re.findall(r"\w+", normalize_show_title(left)))
    right_words = set(re.findall(r"\w+", normalize_show_title(right)))
    if not left_words.intersection(right_words):
        return 0.0
    return SequenceMatcher(
        None,
        fold_diacritics(normalize_show_title(left)),
        fold_diacritics(normalize_show_title(right)),
    ).ratio()


def _year_of(candidate):
    value = (candidate.get("first_air_date") or "")[:4]
    if value.isdigit():
        return int(value)
    year = candidate.get("year")
    return year if isinstance(year, int) else None


def _candidate_season_hint(candidate):
    title = (
        candidate.get("name") or candidate.get("title")
        or candidate.get("original_name") or ""
    )
    _base, hint = parse_tv_season_hint(title)
    if hint is not None:
        return hint
    numeric_sequel = re.search(r"(?<!\d)\s+(\d{1,3})$", title)
    if numeric_sequel:
        number = int(numeric_sequel.group(1))
        if not 1900 <= number <= 2100:
            return number
    return None


def _popularity(candidate):
    try:
        return float(candidate.get("popularity") or 0)
    except (TypeError, ValueError):
        return 0.0


_ACRONYMS = {
    "svu": "Special Victims Unit",
    "himym": "How I Met Your Mother",
    "tbbt": "The Big Bang Theory",
    "twd": "The Walking Dead",
    "b99": "Brooklyn Nine-Nine",
}


def _expand_acronyms(title):
    words = title.split()
    expanded = [_ACRONYMS.get(word.lower(), word) for word in words]
    return " ".join(expanded) if expanded != words else None


def _query_variants(title, _year=None):
    variants = []
    seen = set()
    punctuation_free = " ".join(
        re.sub(r"[\W_]+", " ", title).split(),
    )
    candidates = [(title, None), (punctuation_free, None)]
    expanded = _expand_acronyms(title)
    if expanded:
        candidates.append((expanded, None))
    without_country = _COUNTRY_SUFFIX_RE.sub("", title).strip()
    candidates.append((without_country or title, None))
    without_article = _LEADING_ARTICLE_RE.sub("", without_country or title)
    candidates.append((without_article or without_country or title, None))
    for query, search_year in candidates:
        query = query.strip()
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            variants.append((query.strip(), search_year))
    return variants


def _separate_year(title, year):
    match = _EMBEDDED_YEAR_RE.search(title or "")
    if not match:
        return (title or "").strip(), year
    embedded_year = int(match.group(1) or match.group(2))
    clean_title = (title[:match.start()] or "").strip(" ._-")
    return clean_title, year if year is not None else embedded_year


class ShowResolver:
    def __init__(
        self, key, language, overrides_path=None, client_factory=None,
        providers=None,
    ):
        if overrides_path is None:
            migrate_user_data()
        self._config = (key.strip(), language, providers)
        # client_factory(key, language, providers, operation_cache) -> client
        self.client_factory = client_factory
        self._overrides_path = (
            Path(overrides_path) if overrides_path
            else SHOW_OVERRIDES_FILE
        )
        self._lock = threading.Lock()
        self.load_error = None
        try:
            self._overrides = self._load_overrides()
        except RuntimeError as error:
            self._overrides = {}
            self.load_error = error

    def configure(self, key, language, providers=None):
        with self._lock:
            self._config = (key.strip(), language, providers)

    @property
    def configuration(self):
        with self._lock:
            return self._config

    def _load_overrides(self):
        try:
            data = json.loads(self._overrides_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(
                f"Could not read TV show overrides: {error}",
            ) from error
        if not isinstance(data, dict):
            raise RuntimeError("TV show overrides file must contain an object.")
        overrides = {}
        for title, tmdb_id in data.items():
            normalized = normalize_show_title(title)
            if (
                not normalized or isinstance(tmdb_id, bool)
                or not isinstance(tmdb_id, int) or tmdb_id < 1
            ):
                raise RuntimeError(
                    "TV show overrides contain an invalid title or TMDB ID.",
                )
            overrides[normalized] = tmdb_id
        return overrides

    def remember(
        self, title, tmdb_id, name=None, year=None, candidate=None,
    ):
        key = normalize_show_title(title)
        if not key or not isinstance(tmdb_id, int) or tmdb_id < 1:
            raise ValueError("A title and valid TMDB show ID are required.")
        result = ShowResult(
            ShowState.RESOLVED,
            tmdb_id,
            name or title,
            year,
            1.0,
            candidate=candidate,
        )
        if is_volatile(tmdb_id):
            return result  # Offline Guess results have no stable id to remember
        with self._lock:
            overrides = dict(self._overrides)
            overrides[key] = tmdb_id
            payload = json.dumps(
                overrides, ensure_ascii=False, indent=2,
            )
            try:
                temporary = self._overrides_path.with_suffix(".tmp")
                temporary.write_text(payload + "\n", encoding="utf-8")
                temporary.replace(self._overrides_path)
            except OSError as error:
                raise RuntimeError(
                    f"Could not save TV show override: {error}",
                ) from error
            self._overrides = overrides
        return result

    def resolve_show(
        self, title, year=None, operation_cache=None, season_hint=None,
        episode_number=None, season_number=None,
    ):
        title, year = _separate_year(title, year)
        normalized = normalize_show_title(title)
        if not normalized:
            return ShowResult(ShowState.NOT_FOUND)
        with self._lock:
            config = self._config

        def resolve():
            try:
                return self._resolve(
                    title, year, config, operation_cache, season_hint,
                    episode_number, season_number,
                )
            except TMDBError as error:
                return ShowResult(ShowState.ERROR, error=str(error))

        if operation_cache is None:
            return resolve()
        return operation_cache.get_or_load(
            (
                "show", config, normalized, year, season_hint,
                episode_number, season_number,
            ),
            resolve,
            cache_result=lambda result: result.state != ShowState.ERROR,
        )

    def _resolve(
        self, title, year, config, operation_cache=None, season_hint=None,
        episode_number=None, season_number=None,
    ):
        title, year = _separate_year(title, year)
        api_key, language, providers = config
        if not api_key and self.client_factory is None:
            return ShowResult(
                ShowState.ERROR, error="A TMDB API key is required.",
            )
        normalized = normalize_show_title(title)
        with self._lock:
            override_id = self._overrides.get(normalized)
        if (
            override_id and providers
            and providers.selected != "automatic"
            and provider_of(override_id) != providers.selected
        ):
            override_id = None
        if self.client_factory is not None:
            tmdb = self.client_factory(
                api_key, language, providers, operation_cache,
            )
        else:
            tmdb = TMDB(
                api_key, language, operation_cache=operation_cache,
            )
        if override_id:
            details = tmdb.tv(override_id)
            if not details and not is_volatile(override_id):
                return ShowResult(
                    ShowState.ERROR,
                    error=f"TMDB show {override_id} could not be loaded.",
                )
            if details:
                name = details.get("name") or title
                return ShowResult(
                    ShowState.RESOLVED, override_id, name,
                    _year_of(details), 1.0, candidate=details,
                )

        generic = set(normalized.split()).issubset(_GENERIC_WORDS)
        queries = _query_variants(title, year)
        seen_queries = set()
        alternate_title = None
        ranked_candidates = {}

        def find_candidates(query):
            nonlocal alternate_title
            dbg and dbg.trace_stage(title, "provider_query", query)  # DEBUG-TRACE
            cache_key = ("show_search", config, query.casefold())

            def search():
                return tmdb.search_tv_strict(query, None)

            try:
                candidates = (
                    operation_cache.get_or_load(cache_key, search)
                    if operation_cache is not None else search()
                ) or []
            except Exception:
                dbg and dbg.trace_stage(title, "top_candidates", [])  # DEBUG-TRACE
                raise
            ranked = []
            for candidate in candidates:
                name = candidate.get("name") or candidate.get("title")
                show_id = candidate.get("id")
                if not name or not isinstance(show_id, int) or show_id < 1:
                    continue
                titles = [
                    candidate.get(field)
                    for field in (
                        "name", "title", "original_name",
                        "native_name", "alternate_name",
                    )
                    if candidate.get(field)
                ]
                title_scores = [
                    _similarity(title, candidate_title)
                    for candidate_title in titles
                ]
                confidence = max(title_scores, default=0.0)
                if confidence < 0.5:
                    continue
                displayed_score = _similarity(title, name)
                candidate_hint = _candidate_season_hint(candidate)
                format_name = str(candidate.get("format") or "").upper()
                suffix_kind = bool(re.search(
                    r"(?i)(?:^|[\s._-])(?:OVA|SPECIALS?|MOVIE|PV|CM)\s*\d*$",
                    name,
                ))
                ineligible_base = (
                    format_name in {"OVA", "SPECIAL", "MOVIE", "PV", "CM"}
                    or suffix_kind
                )
                if (
                    generic
                    and normalize_show_title(name) != normalized
                ):
                    continue
                row = {
                    "confidence": confidence,
                    "displayed_score": displayed_score,
                    "year_match": bool(
                        year is not None and _year_of(candidate) == year
                    ),
                    "popularity": _popularity(candidate),
                    "candidate": candidate,
                    "season_hint": candidate_hint,
                    "ineligible_base": ineligible_base,
                    "provider": candidate.get("provider"),
                }
                ranked.append(row)
                previous = ranked_candidates.get(show_id)
                if previous is None or (
                    row["displayed_score"], row["confidence"]
                ) > (
                    previous["displayed_score"], previous["confidence"]
                ):
                    ranked_candidates[show_id] = row
                if alternate_title is None and confidence >= 0.85:
                    alternate_title = (
                        candidate.get("original_name")
                        or candidate.get("alternate_name")
                        or candidate.get("native_name")
                    )
            ranked.sort(
                key=lambda row: (
                    row["displayed_score"], row["confidence"],
                    row["year_match"], row["popularity"],
                ),
                reverse=True,
            )
            dbg and dbg.trace_stage(title, "top_candidates", ranked[:5])  # DEBUG-TRACE

        for query, _search_year in queries:
            key = query.casefold()
            if key in seen_queries:
                continue
            seen_queries.add(key)
            find_candidates(query)

        if alternate_title:
            alternate_title = str(alternate_title).strip()
            key = alternate_title.casefold()
            if alternate_title and key not in seen_queries:
                find_candidates(alternate_title)

        rows = list(ranked_candidates.values())
        rows = [
            row for row in rows
            if row["confidence"] >= 0.85
        ]
        if not rows:
            return ShowResult(ShowState.NOT_FOUND)

        is_anilist = any(
            row["provider"] == "anilist"
            for row in rows
        ) or bool(providers and providers.selected == "anilist")
        ambiguous = False
        season_mismatch = False
        anime_season_index = 1
        if is_anilist:
            rows.sort(key=lambda row: (
                _year_of(row["candidate"]) or 9999,
                row["candidate"].get("id", 0),
            ))
            series_rows = [
                row for row in rows if not row["ineligible_base"]
            ] or rows
            for index, row in enumerate(series_rows, start=1):
                row["anime_season_index"] = index

            if season_hint is not None:
                matched = [
                    row for row in series_rows
                    if row["season_hint"] == season_hint
                ]
                if season_hint == "final":
                    matched = [
                        row for row in series_rows
                        if parse_tv_season_hint(
                            row["candidate"].get("name")
                            or row["candidate"].get("title") or ""
                        )[1] == "final"
                    ] or [series_rows[-1]]
                elif not matched and isinstance(season_hint, int):
                    matched = [
                        row for row in series_rows
                        if row["anime_season_index"] == season_hint
                    ]
                if matched:
                    rows = matched
                else:
                    season_mismatch = True
                    rows = [series_rows[0]]
            else:
                base_rows = [
                    row for row in series_rows
                    if row["season_hint"] is None
                ]
                if base_rows:
                    rows = base_rows

        rows.sort(key=lambda row: (
            row["displayed_score"],
            row["confidence"],
            row["year_match"],
            not row["ineligible_base"],
            -(_year_of(row["candidate"]) or 9999)
            if is_anilist and season_hint is None else 0,
            row["popularity"],
        ), reverse=True)

        if episode_number is not None and len(rows) > 1:
            best_score = rows[0]["displayed_score"]
            tied = [
                row for row in rows
                if abs(row["displayed_score"] - best_score) <= 0.01
            ]
            if len(tied) > 1 and len({
                _year_of(row["candidate"]) for row in tied
            }) > 1:
                available = []
                for row in tied:
                    candidate = row["candidate"]
                    candidate_id = candidate.get("id")
                    lookup_season = (
                        1 if is_anilist
                        else season_number or season_hint or 1
                    )
                    season_data = tmdb.season(
                        candidate_id, lookup_season,
                    ) or {}
                    if any(
                        episode.get("episode_number") == episode_number
                        for episode in season_data.get("episodes") or []
                    ):
                        available.append(row)
                if len(available) == 1:
                    rows = available
                else:
                    ambiguous = True

        selected = rows[0]
        candidate = dict(selected["candidate"])
        if is_anilist:
            anime_season_index = selected["anime_season_index"]
            if (
                isinstance(season_hint, int)
                and selected["season_hint"] == season_hint
            ):
                anime_season_index = season_hint
            candidate["_anime_season_index"] = anime_season_index
            candidate["_anime_season_mismatch"] = season_mismatch
        show_id = candidate["id"]
        return ShowResult(
            ShowState.RESOLVED,
            show_id,
            candidate.get("name") or candidate.get("title"),
            _year_of(candidate),
            selected["confidence"],
            candidate=candidate,
            ambiguous=ambiguous,
        )
