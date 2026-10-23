from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
import json
from pathlib import Path
import re
import threading
from typing import Optional

from .config import SHOW_OVERRIDES_FILE, migrate_user_data
from .naming import fold_diacritics
from .providers.ids import is_volatile
from .tmdb import TMDB, TMDBError


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


_GENERIC_WORDS = {"the", "show", "name", "example", "episode"}
_COUNTRY_SUFFIX_RE = re.compile(r"(?i)\s+(?:US|UK|AU)$")
_LEADING_ARTICLE_RE = re.compile(r"(?i)^the\s+")


def normalize_show_title(title):
    return " ".join(re.findall(r"\w+", (title or "").casefold()))


def normalize_show_key(title, year=None):
    return normalize_show_title(title), year


def _similarity(left, right):
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


def _query_variants(title, year):
    variants = []
    seen = set()
    candidates = [(title, year)]
    expanded = _expand_acronyms(title)
    if expanded:
        candidates.append((expanded, year))
    without_country = _COUNTRY_SUFFIX_RE.sub("", title).strip()
    candidates.append((without_country or title, None))
    without_article = _LEADING_ARTICLE_RE.sub("", without_country or title)
    candidates.append((without_article or without_country or title, None))
    for query, search_year in candidates:
        key = (normalize_show_title(query), search_year)
        if query.strip() and key not in seen:
            seen.add(key)
            variants.append((query.strip(), search_year))
    return variants[:3]


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
            return result  # offline guesses have no stable id to remember
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

    def resolve_show(self, title, year=None, operation_cache=None):
        normalized = normalize_show_title(title)
        if not normalized:
            return ShowResult(ShowState.NOT_FOUND)
        with self._lock:
            config = self._config

        def resolve():
            try:
                return self._resolve(
                    title, year, config, operation_cache,
                )
            except TMDBError as error:
                return ShowResult(ShowState.ERROR, error=str(error))

        if operation_cache is None:
            return resolve()
        return operation_cache.get_or_load(
            ("show", config, normalized, year),
            resolve,
            cache_result=lambda result: result.state != ShowState.ERROR,
        )

    def _resolve(self, title, year, config, operation_cache=None):
        api_key, language, providers = config
        if not api_key and self.client_factory is None:
            return ShowResult(
                ShowState.ERROR, error="A TMDB API key is required.",
            )
        normalized = normalize_show_title(title)
        with self._lock:
            override_id = self._overrides.get(normalized)
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
        for query, search_year in _query_variants(title, year):
            candidates = tmdb.search_tv_strict(query, search_year)
            ranked = []
            for candidate in candidates:
                name = candidate.get("name") or candidate.get("title")
                if not name:
                    continue
                displayed_score = _similarity(title, name)
                original_name = candidate.get("original_name")
                original_score = (
                    min(_similarity(title, original_name), 0.85)
                    if original_name else 0.0
                )
                confidence = max(displayed_score, original_score)
                if generic and normalize_show_title(name) != normalized:
                    continue
                ranked.append((
                    confidence,
                    bool(year and _year_of(candidate) == year),
                    _popularity(candidate),
                    candidate,
                ))
            ranked.sort(key=lambda row: (row[0], row[2]), reverse=True)
            best = ranked[0] if ranked else None
            if best:
                near_top = [
                    row for row in ranked
                    if best[0] - row[0] <= 0.05
                ]
                if len(near_top) > 1:
                    best = max(
                        near_top,
                        key=lambda row: (row[1], row[2], row[0]),
                    )
            if best and best[0] >= 0.85:
                confidence, _year_match, _candidate_popularity, candidate = best
                show_id = candidate.get("id")
                if isinstance(show_id, int) and show_id > 0:
                    return ShowResult(
                        ShowState.RESOLVED,
                        show_id,
                        candidate.get("name") or candidate.get("title"),
                        _year_of(candidate),
                        confidence,
                        candidate=candidate,
                    )
        return ShowResult(ShowState.NOT_FOUND)
