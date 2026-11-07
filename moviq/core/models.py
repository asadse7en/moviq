from pathlib import Path
import re

from .config import VIDEO_EXT
from .. import trace

from .naming import (
    _clean_tv_title, _useful_tv_title, norm, parse_name,
    parse_production_code, parse_tv_air_date, parse_tv_compact_episode,
    parse_tv_episode,
    parse_tv_episode_in_context, parse_tv_episode_end,
    parse_tv_episode_title, parse_tv_episode_title_in_context,
    parse_tv_extra, parse_tv_name, parse_tv_season_hint,
    TV_NAME_MARKER_RE, tv_season_is_explicit,
)


_SEASON_DIR_RE = re.compile(
    r"(?i)^(?:season|s)\s*(\d+)$"
)
_SPECIALS_DIR_RE = re.compile(r"(?i)^specials?$")
_NUMBERED_STEM_RE = re.compile(
    r"(?i)^(?:e\s*)?0*(?P<episode>\d{1,5})"
    r"(?:\s*-\s*(?:e\s*)?0*(?P<episode_end>\d{1,5}))?$"
)
_GENERIC_DIRS = {
    "context", "download", "downloads", "episode", "episodes", "folder",
    "library", "media", "movies", "new folder", "season", "series",
    "seasons", "show folders", "shows", "special", "specials", "tv", "tv shows",
    "video", "videos",
}
_LIBRARY_DIRS = {
    "downloads", "library", "media", "movies", "shows", "tv", "tv shows",
    "videos",
}


def _useful_show_dir(name):
    title = _clean_tv_title(name)
    normalized = norm(title)
    return bool(
        normalized
        and normalized not in _GENERIC_DIRS
        and not _SEASON_DIR_RE.fullmatch(name.strip())
        and _useful_tv_title(title)
    )


def _show_context(path, root):
    path, root = Path(path), Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None:
        for part in reversed(relative.parts[:-1]):
            if _useful_show_dir(part):
                return _clean_tv_title(part)
    if _useful_show_dir(root.name):
        return _clean_tv_title(root.name)
    if norm(root.name) in _LIBRARY_DIRS:
        return None
    for parent in path.parent.parents:
        if _useful_show_dir(parent.name):
            return _clean_tv_title(parent.name)
    return None


def _season_context(path, root):
    show_title = _show_context(path, root)
    for parent in (path.parent, *path.parent.parents):
        match = _SEASON_DIR_RE.fullmatch(parent.name.strip())
        if match:
            number = int(match.group(1))
            if not 1900 <= number <= 2100:
                return number
        if norm(parent.name) in ("special", "specials"):
            return 0
        if show_title and norm(parent.name) == norm(show_title):
            break
    return 1


def _folder_show_context(path, root):
    """Find the nearest useful show folder without crossing the scan root."""
    path, root = Path(path), Path(root)
    try:
        path.relative_to(root)
    except ValueError:
        return None

    parent = path.parent
    while True:
        if parent == root:
            return _clean_tv_title(root.name) if _useful_show_dir(
                root.name,
            ) else None
        if (
            _useful_show_dir(parent.name)
            and not _SPECIALS_DIR_RE.fullmatch(parent.name.strip())
        ):
            return _clean_tv_title(parent.name)
        if parent.parent == parent:
            return None
        try:
            parent.parent.relative_to(root)
        except ValueError:
            return None
        parent = parent.parent


def _season_folder_number(path, root):
    """Return a season directory number, bounded to the selected scan root."""
    path, root = Path(path), Path(root)
    try:
        path.relative_to(root)
    except ValueError:
        return None

    parent = path.parent
    while True:
        season = _SEASON_DIR_RE.fullmatch(parent.name.strip())
        if season:
            number = int(season.group(1))
            if not 1900 <= number <= 2100:
                return number
        if _SPECIALS_DIR_RE.fullmatch(parent.name.strip()):
            return 0
        if parent == root or parent.parent == parent:
            return None
        try:
            parent.parent.relative_to(root)
        except ValueError:
            return None
        parent = parent.parent


def _is_movies_folder(path, root):
    path, root = Path(path), Path(root)
    if norm(root.name) == "movies":
        return True
    try:
        path.relative_to(root)
    except ValueError:
        return norm(path.parent.name) == "movies"
    for parent in (path.parent, *path.parent.parents):
        if norm(parent.name) == "movies":
            return True
        if parent == root:
            break
    return False


def _numeric_sibling_sequence(path):
    siblings = []
    try:
        for sibling in path.parent.iterdir():
            if (
                sibling == path
                or not sibling.is_file()
                or sibling.suffix.lower() not in VIDEO_EXT
            ):
                continue
            if not _NUMBERED_STEM_RE.fullmatch(sibling.stem):
                siblings.append(False)
            else:
                siblings.append(True)
    except OSError:
        return False
    return bool(siblings) and sum(siblings) > len(siblings) / 2


class Item:
    def __init__(self, path, root, media_type=None):
        self.path = Path(path)
        self.root = Path(root)
        trace.trace_start(lambda: str(self.path))
        self.orig_path = self.path
        self.orig_root = self.root
        self.orig_name = self.path.name
        self.orig_folder = self.path.parent.name
        self.kind = parse_tv_extra(self.path.stem)
        self.air_date = parse_tv_air_date(self.path.stem)
        parsed_tv_name = parse_tv_name(self.path, self.root)
        parsed_year = parsed_tv_name[1]
        parsed_episode = parsed_tv_name[3]
        self.movie_like = bool(
            self.kind is None
            and (
                _is_movies_folder(self.path, self.root)
                or (
                    parsed_year is not None
                    and parsed_episode is None
                    and self.air_date is None
                )
            )
        )
        compact_episode = parse_tv_compact_episode(self.path.stem)
        self.compact_episode_number = (
            compact_episode[2] if compact_episode else None
        )
        tv_episode = parse_tv_episode(self.path.stem)
        trace.trace_stage(
            lambda: str(self.path), "episode_marker", tv_episode,
        )
        marker = TV_NAME_MARKER_RE.search(self.path.stem)
        marker_groups = marker.groupdict() if marker else {}
        marker_has_season = any(
            marker_groups.get(name)
            for name in ("season", "season_x", "season_word")
        )
        marker_episode = (
            tv_episode[3] if tv_episode else
            int(marker_groups["bare_episode"])
            if marker_groups.get("bare_episode") else None
        )
        marker_season = (
            tv_episode[2] if tv_episode and marker_has_season
            else int(
                marker_groups.get("season")
                or marker_groups.get("season_x")
                or marker_groups.get("season_word")
            )
            if marker_has_season else None
        )
        marker_episode_end = parse_tv_episode_end(self.path)
        numbered_stem = _NUMBERED_STEM_RE.fullmatch(self.path.stem)
        folder_title = None
        marker_title = tv_episode[0] if tv_episode else ""
        needs_folder_title = (
            bool(numbered_stem)
            or (
                tv_episode is not None
                and not _useful_tv_title(marker_title)
            )
        )
        if needs_folder_title:
            folder_title = _folder_show_context(self.path, self.root)
            if folder_title:
                trace.trace_stage(
                    lambda: str(self.path), "folder_title", folder_title,
                )

        if numbered_stem:
            numbered_episode = int(numbered_stem.group("episode"))
            numbered_end = numbered_stem.group("episode_end")
        else:
            numbered_episode = numbered_end = None

        has_named_episode_marker = marker is not None
        numeric_sequence = bool(
            numbered_stem and _numeric_sibling_sequence(self.path)
        )
        numbered_stem_has_context = (
            has_named_episode_marker
            or (
                tv_episode is not None
                and marker_title.strip()
                and not marker_title.strip().isdigit()
            )
            or bool(folder_title and (tv_episode or numeric_sequence))
        )
        context_episode = (
            numbered_episode
            if numbered_episode is not None
            and numbered_stem_has_context
            else None
        )
        context_end = (
            int(numbered_end)
            if numbered_end is not None and context_episode is not None
            else None
        )
        has_context_episode = (
            context_episode is not None
            or (
                marker_episode is not None
                and not (marker_title and not marker_title.isdigit())
            )
        )
        folder_context_tv = bool(
            folder_title and has_context_episode
        )
        filename_title_usable = (
            _useful_tv_title(marker_title)
            and not (numbered_stem and marker_title.isdigit())
        )
        self.media_type = (
            "movie" if self.movie_like
            else "tv" if folder_context_tv
            else media_type or (
                "tv" if tv_episode or self.air_date or self.kind
                else "movie"
            )
        )
        contextual_episode_title = None
        self.season_hint = parse_tv_season_hint(self.path.stem)[1]
        if self.season_hint is None:
            for parent in self.path.parents:
                _parent_title, parent_hint = parse_tv_season_hint(
                    parent.name,
                )
                if parent_hint is not None:
                    self.season_hint = parent_hint
                    break
        if self.season_hint is None:
            season_folder_hint = _season_folder_number(
                self.path, self.root,
            )
            if season_folder_hint and season_folder_hint > 0:
                self.season_hint = season_folder_hint
        if self.season_hint is None and marker_season:
            self.season_hint = marker_season
        if self.kind:
            self.season_hint = 0
        if self.media_type == "tv":
            show_title, show_year, season, episode = parsed_tv_name
            trace.trace_stage(
                lambda: str(self.path), "tv_name",
                lambda: (show_title, show_year, season, episode),
            )
            self.season_was_explicit = tv_season_is_explicit(self.path)
            self.season_was_explicit = bool(
                self.season_was_explicit or self.season_hint is not None
            )
            if compact_episode:
                self.season_was_explicit = True
            self.episode_end_number = parse_tv_episode_end(self.path)
            if folder_context_tv and not filename_title_usable:
                show_title = folder_title
                episode = context_episode or marker_episode
                season = marker_season
                if season is None:
                    season = _season_folder_number(self.path, self.root)
                if season is None:
                    season = 1
                if context_end is not None:
                    self.episode_end_number = context_end
            if episode is None or not show_title:
                contextual_episode = parse_tv_episode_in_context(
                    self.path.stem,
                    _show_context(self.path, self.root),
                    _season_context(self.path, self.root),
                )
                if contextual_episode:
                    show_title, show_year, season, episode = (
                        contextual_episode
                    )
                    self.episode_end_number = None
                    contextual_episode_title = (
                        parse_tv_episode_title_in_context(self.path.stem)
                    )
            if (
                episode is not None and isinstance(self.season_hint, int)
                and self.season_hint > 0
            ):
                season = self.season_hint
            tv_episode = (
                (show_title, show_year, season or 1, episode)
                if episode is not None and show_title else None
            )
            self.episode_title = (
                contextual_episode_title
                or parse_tv_episode_title(self.path.stem)
            )
            self.production_code = (
                parse_production_code(self.path.stem)
                if episode is None else None
            )
            self.is_tv_episode = bool(
                tv_episode or self.air_date
                or (show_title and self.episode_title) or self.kind
            )
        else:
            self.is_tv_episode = False
            self.episode_title = None
            self.production_code = None
            season_folder = _season_folder_number(self.path, self.root)
            self.season_was_explicit = bool(
                marker_has_season or season_folder is not None
            )
            self.episode_end_number = marker_episode_end or context_end
        if self.media_type == "tv":
            self.query, self.year = show_title, show_year
            self.season_number = (
                0 if self.kind
                else season if episode is not None else None
            )
            self.episode_number = episode
            self.episode_numbers = (
                tuple(range(episode, self.episode_end_number + 1))
                if episode is not None
                and self.episode_end_number is not None
                and self.episode_end_number > episode
                else ((episode,) if episode is not None else ())
            )
        else:
            self.query, self.year = parse_name(self.path.stem)
            trace.trace_stage(
                lambda: str(self.path), "movie_name",
                lambda: (self.query, self.year),
            )
            parsed_marker_episode = (
                None
                if numbered_stem and not folder_title
                and not has_named_episode_marker
                else marker_episode
            )
            parsed_episode = (
                context_episode
                if context_episode is not None else parsed_marker_episode
            )
            self.season_number = (
                marker_season
                if marker_season is not None
                else season_folder if parsed_episode is not None else None
            )
            if parsed_episode is not None and self.season_number is None:
                self.season_number = 1
            self.episode_number = parsed_episode
            self.episode_numbers = (
                (parsed_episode,) if parsed_episode is not None else ()
            )
            if (
                self.episode_end_number is not None
                and parsed_episode is not None
                and self.episode_end_number > parsed_episode
            ):
                self.episode_numbers = tuple(
                    range(parsed_episode, self.episode_end_number + 1),
                )
        self.show_title = self.query
        if not self.query and self.media_type != "tv":
            self.query = self.path.parent.name
        trace.trace_stage(
            lambda: str(self.path), "parsed_query",
            lambda: {
                "media_type": self.media_type,
                "query": self.query,
                "year": self.year,
            },
        )
        if self.air_date:
            trace.trace_stage(
                lambda: str(self.path), "air_date", self.air_date,
            )
        self.match = None
        self.match_source = None
        self.match_method = None
        self.tried_sources = []
        self.title_score = 0.0
        self.status = "pending"
        self._pending_manual_show = None
        self.status_detail = ""
        self.error_code = None
        self.review_reason = None
        self.error = ""
        self.iid = None
