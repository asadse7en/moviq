import json
import logging
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from typing import Callable, Dict

from .config import (
    CONFIG_FILE, LEGACY_CONFIG_FILE, migrate_user_data,
)
from .formatting import (
    CUSTOM_ID, MOVIE_DEFAULT_ID, MOVIE_FORMATS, TV_DEFAULT_ID, TV_FORMATS,
    format_component,
)
from .tmdb import TMDB, TMDBAuthError, TMDBError


MOVIE_PRESETS = (
    "{title} ({year})",
    "{title} ({year}) [tmdbid-{id}]",
    "{title}.{year}",
    "{title} - {year}",
    "{title} [{year}]",
    "{title} ({year}) - {id}",
    "{year} - {title}",
)
TV_PRESETS = (
    "{title} - S{season:00}E{episode:00} - {episode_title}",
    "{title} - S{season:00}E{episode:00}",
    "{title} ({year}) - S{season:00}E{episode:00} - {episode_title}",
    "{title} - {season}x{episode:00}",
    "S{season:00}E{episode:00} - {episode_title}",
    "S{season:00}E{episode:00} {episode_title}",
    "{season}x{episode:00} {episode_title}",
)
MOVIE_SAMPLE = {"title": "Inception", "year": 2010, "id": 27205}
TV_SAMPLE = {
    "title": "Breaking Bad",
    "year": 2008,
    "id": 1396,
    "season": 1,
    "episode": 1,
    "episode_title": "Pilot",
}
DEFAULTS = {
    "remember": True,
    "key": "",
    "theme": "system",
    "language": "en-US  English",
    "threshold": 90,
    "auto_match": True,
    "confirm_apply": True,
    "name_template": MOVIE_PRESETS[0],
    "tag_id": False,
    "media_mode": "movie",
    "tv_template": TV_PRESETS[0],
    "movie_folder": True,
    "tv_folder": True,
    "rename_subtitles": True,
    "library_mode": "in_place",
    "movies_library": "",
    "tv_library": "",
    "window_geometry": "",
    "window_state": "normal",
    "last_folder": "",
    "movie_preset_id": MOVIE_DEFAULT_ID,
    "movie_folder_pattern": "{title} ({year})",
    "movie_file_pattern": "{title} ({year})",
    "movie_create_folder": True,
    "movie_custom_folder_pattern": "",
    "movie_custom_file_pattern": "",
    "tv_preset_id": TV_DEFAULT_ID,
    "tv_show_folder_pattern": "{title}",
    "tv_season_folder_pattern": "Season {season:00}",
    "tv_file_pattern": (
        "{title} - S{season:00}E{episode:00} - {episode_title}"
    ),
    "tv_create_show_folder": True,
    "tv_create_season_folder": True,
    "tv_custom_show_folder_pattern": "",
    "tv_custom_season_folder_pattern": "",
    "tv_custom_file_pattern": "",
}
log = logging.getLogger("moviq.ui.settings")


class SettingsStore:
    """Load and debounced-save user preferences while preserving unknown keys."""

    def __init__(
        self,
        path=CONFIG_FILE,
        legacy_path=LEGACY_CONFIG_FILE,
        on_change=None,
        on_error=None,
        defaults=None,
        save_delay: float = 0.5,
        on_batch_done=None,
    ):
        self.path = Path(path)
        self.legacy_path = Path(legacy_path)
        if self.path == CONFIG_FILE and self.legacy_path == LEGACY_CONFIG_FILE:
            migrate_user_data()
        self.on_change: Callable[[str, object], None] = (
            on_change or (lambda _key, _value: None)
        )
        self.on_error: Callable[[Exception], None] = (
            on_error or (lambda _error: None)
        )
        self.on_batch_done: Callable[[], None] = (
            on_batch_done or (lambda: None)
        )
        self.batching = False
        self.defaults = dict(defaults or DEFAULTS)
        self.save_delay = save_delay
        self._lock = threading.RLock()
        self._timer = None
        self.data = self._load()

    def _load(self):
        config = {}
        source = None
        for path in (self.path, self.legacy_path):
            try:
                with path.open("r", encoding="utf-8") as config_file:
                    loaded = json.load(config_file)
                if isinstance(loaded, dict):
                    config = loaded
                    source = path
                    break
            except FileNotFoundError:
                continue
            except (OSError, json.JSONDecodeError) as error:
                if sys.stderr is not None:
                    log.warning(
                        "Could not read settings from %s: %s", path, error,
                    )
        merged = dict(self.defaults)
        merged.update(config)
        cache_setting_keys = [
            key for key in merged
            if "cache" in str(key).casefold()
        ]
        removed_setting_found = any(
            key in merged
            for key in ("keep_undo", "app_language", "check_updates")
        ) or bool(cache_setting_keys)
        merged.pop("keep_undo", None)
        merged.pop("app_language", None)
        merged.pop("check_updates", None)
        for key in cache_setting_keys:
            merged.pop(key, None)
        if merged.get("theme") not in ("light", "dark", "system"):
            merged["theme"] = self.defaults["theme"]
        for key in (
            "remember", "auto_match", "confirm_apply", "tag_id",
            "movie_folder", "tv_folder", "rename_subtitles",
            "movie_create_folder", "tv_create_show_folder",
            "tv_create_season_folder",
        ):
            if not isinstance(merged.get(key), bool):
                merged[key] = self.defaults[key]
        try:
            threshold = int(round(float(merged["threshold"]) / 5) * 5)
        except (TypeError, ValueError, OverflowError):
            threshold = self.defaults["threshold"]
        merged["threshold"] = min(100, max(50, threshold))
        if merged.get("library_mode") not in ("in_place", "library"):
            merged["library_mode"] = self.defaults["library_mode"]
        for key in (
            "key", "language", "name_template", "tv_template",
            "window_geometry", "last_folder", "movies_library",
            "tv_library",
            "movie_preset_id", "movie_folder_pattern",
            "movie_file_pattern", "movie_custom_folder_pattern",
            "movie_custom_file_pattern", "tv_preset_id",
            "tv_show_folder_pattern", "tv_season_folder_pattern",
            "tv_file_pattern", "tv_custom_show_folder_pattern",
            "tv_custom_season_folder_pattern", "tv_custom_file_pattern",
        ):
            if not isinstance(merged.get(key), str):
                merged[key] = self.defaults[key]
        formatting_migrated = _migrate_formatting_settings(merged, config)
        if merged.get("media_mode") not in ("movie", "tv"):
            merged["media_mode"] = self.defaults["media_mode"]
        if merged.get("window_state") not in ("normal", "zoomed"):
            merged["window_state"] = self.defaults["window_state"]
        if not merged["remember"]:
            merged.pop("key", None)
            merged.pop("provider_keys", None)
        if (
            (source == self.legacy_path and config)
            or removed_setting_found
            or formatting_migrated
        ):
            self.data = merged
            self._schedule_save()
        return merged

    def set(self, key: str, value: object) -> None:
        """Update one preference, notify the app, and schedule persistence."""
        with self._lock:
            self.data[key] = value
            if key == "remember" and not value:
                self.data.pop("key", None)
                self.data.pop("provider_keys", None)
            self._schedule_save()
        self.on_change(key, value)

    def update(self, values: Dict[str, object]) -> None:
        """Update related settings as one debounced write."""
        with self._lock:
            missing = object()
            changed = {
                key: value for key, value in values.items()
                if self.data.get(key, missing) != value
            }
            self.data.update(values)
            if not self.data.get("remember", True):
                self.data.pop("key", None)
                self.data.pop("provider_keys", None)
            self._schedule_save()
        self.batching = True
        try:
            for key, value in changed.items():
                self.on_change(key, value)
        finally:
            self.batching = False
        self.on_batch_done()

    def _schedule_save(self):
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(self.save_delay, self._save_in_background)
        self._timer.daemon = True
        self._timer.start()

    def _persistable_data(self):
        data = dict(self.data)
        for key in tuple(data):
            if "cache" in str(key).casefold():
                data.pop(key, None)
        if not data.get("remember", True):
            data.pop("key", None)
            data.pop("provider_keys", None)
        return data

    def _save_in_background(self):
        try:
            self.save_now()
        except OSError as error:
            self.on_error(error)

    def save_now(self) -> None:
        """Write settings atomically; propagate failures to the caller."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp",
                dir=str(self.path.parent),
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as config_file:
                    json.dump(
                        self._persistable_data(), config_file,
                        ensure_ascii=False, indent=2,
                    )
                    config_file.flush()
                    os.fsync(config_file.fileno())
                os.replace(temp_name, self.path)
            except OSError:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass
                raise

    def close(self) -> None:
        """Flush pending changes and stop the save timer."""
        self.save_now()


def resolve_system_theme(fallback: str = "dark") -> str:
    """Resolve the current Windows app theme without adding dependencies."""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if value else "dark"
        except OSError:
            pass
    return fallback if fallback in ("light", "dark") else "dark"


def check_tmdb_key(key: str) -> str:
    """Check a v3 key asynchronously at the caller and return a UI status."""
    if not key:
        return "Not checked"
    if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
        return "Wrong format"
    try:
        return "Connected" if TMDB(key).check() else "Rejected"
    except TMDBAuthError:
        return "Rejected"
    except TMDBError as error:
        return (
            "Rejected" if "(403)" in str(error)
            else "Couldn't reach TMDB"
        )


def _migrate_formatting_settings(merged, config):
    changed = False

    def migrate_type(kind, presets, default_id, fields, old_pattern_key):
        nonlocal changed
        id_key = f"{kind}_preset_id"
        preset_id = config.get(id_key)
        known_ids = {preset[0] for preset in presets} | {CUSTOM_ID}
        if preset_id not in known_ids:
            if id_key in config:
                preset_id = default_id
            else:
                old_pattern = config.get(
                    old_pattern_key, merged[old_pattern_key],
                )
                if kind == "tv":
                    old_pattern = old_pattern.replace(":02d", ":00")
                preset_id = next(
                    (
                        preset[0] for preset in presets
                        if preset[fields[-1][1]] == old_pattern
                    ),
                    CUSTOM_ID,
                )
            changed = True
        merged[id_key] = preset_id

        selected = next(
            (preset for preset in presets if preset[0] == preset_id),
            next(preset for preset in presets if preset[0] == default_id),
        )
        for key, preset_index in fields:
            if preset_id == CUSTOM_ID:
                custom_key = f"{kind}_custom_{key.removeprefix(kind + '_')}"
                if custom_key in config:
                    value = config[custom_key]
                elif key in config:
                    value = config[key]
                elif key.endswith("_file_pattern"):
                    value = config.get(
                        old_pattern_key, merged[old_pattern_key],
                    )
                else:
                    value = selected[preset_index]
                if not isinstance(value, str):
                    value = selected[preset_index]
                elif not value and key.endswith("_file_pattern"):
                    if key not in config and custom_key not in config:
                        value = selected[preset_index]
                if kind == "tv" and isinstance(value, str):
                    value = value.replace(":02d", ":00")
                merged[key] = value if isinstance(value, str) else ""
                merged[custom_key] = merged[key]
            else:
                merged[key] = selected[preset_index]

    movie_fields = (
        ("movie_folder_pattern", 2),
        ("movie_file_pattern", 3),
    )
    tv_fields = (
        ("tv_show_folder_pattern", 2),
        ("tv_season_folder_pattern", 3),
        ("tv_file_pattern", 4),
    )
    migrate_type(
        "movie", MOVIE_FORMATS, MOVIE_DEFAULT_ID, movie_fields,
        "name_template",
    )
    migrate_type(
        "tv", TV_FORMATS, TV_DEFAULT_ID, tv_fields, "tv_template",
    )

    for key in (
        "movie_create_folder", "tv_create_show_folder",
        "tv_create_season_folder",
    ):
        if key not in config:
            changed = True
    if "movie_create_folder" not in config:
        merged["movie_create_folder"] = merged.get("movie_folder", True)
    if "tv_create_show_folder" not in config:
        merged["tv_create_show_folder"] = merged.get("tv_folder", True)
    if "tv_create_season_folder" not in config:
        merged["tv_create_season_folder"] = merged.get("tv_folder", True)

    if (
        "movie_preset_id" not in config and config.get("tag_id")
        and "{id}" not in merged["movie_folder_pattern"]
    ):
        merged["movie_folder_pattern"] += " [tmdbid-{id}]"
        merged["movie_custom_folder_pattern"] = (
            merged["movie_folder_pattern"]
        )
        merged["movie_custom_file_pattern"] = (
            merged["movie_file_pattern"]
        )
        merged["movie_preset_id"] = CUSTOM_ID
        changed = True

    merged["name_template"] = merged["movie_file_pattern"]
    merged["tv_template"] = merged["tv_file_pattern"]
    merged["movie_folder"] = merged["movie_create_folder"]
    merged["tv_folder"] = (
        merged["tv_create_show_folder"]
        and merged["tv_create_season_folder"]
    )
    return changed


def validate_pattern(pattern: str, tokens: set) -> str:
    """Return a short validation message, or an empty string when valid."""
    if not pattern.strip():
        return "Pattern cannot be empty."
    found = re.findall(r"\{([^{}]+)\}", pattern)
    for token in found:
        name, separator, specifier = token.partition(":")
        if name not in tokens:
            return f"Unknown token {{{name}}}."
        if separator and (
            name not in {"season", "episode"}
            or specifier not in {"00", "02d"}
        ):
            return "Pattern contains an invalid token or format."
    if "title" not in {
        token.split(":", 1)[0] for token in found
    }:
        return "Warning: {title} is missing from this pattern."
    return ""


def render_pattern(pattern: str, values: Dict[str, object]) -> str:
    """Compatibility wrapper around the shared safe formatter."""
    return format_component(pattern, values)
