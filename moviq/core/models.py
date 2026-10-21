from pathlib import Path
import re

from .naming import (
    norm, parse_name, parse_production_code, parse_tv_episode,
    parse_tv_episode_in_context, parse_tv_episode_end,
    parse_tv_episode_title, parse_tv_name, tv_season_is_explicit,
)


_SEASON_DIR_RE = re.compile(
    r"(?i)^(?:season|series|s)[ ._-]*(\d{1,2})$"
)
_GENERIC_DIRS = {
    "download", "downloads", "episode", "episodes", "folder", "library",
    "media", "movies", "new folder", "season", "series", "shows", "special",
    "specials", "tv", "tv shows", "video", "videos",
}
_LIBRARY_DIRS = {
    "downloads", "library", "media", "movies", "shows", "tv", "tv shows",
    "videos",
}


def _useful_show_dir(name):
    normalized = norm(name)
    return bool(
        normalized
        and normalized not in _GENERIC_DIRS
        and not _SEASON_DIR_RE.fullmatch(name.strip())
    )


def _show_context(path, root):
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None
    if relative is not None:
        for part in relative.parts[:-1]:
            if _useful_show_dir(part):
                return part
    if _useful_show_dir(root.name):
        return root.name
    if norm(root.name) in _LIBRARY_DIRS:
        return None
    for parent in path.parent.parents:
        if _useful_show_dir(parent.name):
            return parent.name
    return None


def _season_context(path, root):
    show_title = _show_context(path, root)
    for parent in (path.parent, *path.parent.parents):
        match = _SEASON_DIR_RE.fullmatch(parent.name.strip())
        if match:
            return int(match.group(1))
        if norm(parent.name) in ("special", "specials"):
            return 0
        if show_title and norm(parent.name) == norm(show_title):
            break
    return 1


class Item:
    def __init__(self, path, root, media_type=None):
        self.path = Path(path)
        self.root = Path(root)
        self.orig_path = self.path
        self.orig_root = self.root
        self.orig_name = self.path.name
        self.orig_folder = self.path.parent.name
        tv_episode = parse_tv_episode(self.path.stem)
        self.media_type = media_type or ("tv" if tv_episode else "movie")
        if self.media_type == "tv":
            show_title, show_year, season, episode = parse_tv_name(self.path)
            self.season_was_explicit = tv_season_is_explicit(self.path)
            self.episode_end_number = parse_tv_episode_end(self.path)
            if episode is None:
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
            tv_episode = (
                (show_title, show_year, season or 1, episode)
                if episode is not None and show_title else None
            )
            self.episode_title = parse_tv_episode_title(self.path.stem)
            self.production_code = (
                parse_production_code(self.path.stem)
                if episode is None else None
            )
            self.is_tv_episode = bool(
                tv_episode or (show_title and self.episode_title)
            )
        else:
            self.is_tv_episode = False
            self.episode_title = None
            self.production_code = None
            self.season_was_explicit = False
            self.episode_end_number = None
        if self.media_type == "tv":
            self.query, self.year = show_title, show_year
            self.season_number = season if episode is not None else None
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
            self.season_number = None
            self.episode_number = None
            self.episode_numbers = ()
        self.show_title = self.query
        if not self.query and self.media_type != "tv":
            self.query = self.path.parent.name
        self.match = None
        self.match_source = None
        self.status = "pending"
        self._pending_manual_show = None
        self.status_detail = ""
        self.error_code = None
        self.review_reason = None
        self.error = ""
        self.iid = None
