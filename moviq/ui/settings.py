"""Settings persistence, formatting helpers, and the settings window content."""

import json
import logging
import os
import queue
import re
import sys
import tempfile
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk

from PIL import Image, ImageTk
import requests

from .. import __version__ as APP_VERSION, resource
from ..core.config import (
    CONFIG_FILE, LEGACY_CONFIG_FILE, migrate_user_data,
)
from ..core.formatting import (
    CUSTOM_ID, MOVIE_DEFAULT_ID, MOVIE_FORMATS, TV_DEFAULT_ID, TV_FORMATS,
    format_component, format_pattern,
)
from ..core.tmdb import MAX_WORKERS, TMDB, TMDBAuthError, TMDBError
from ..core.updater import (
    GITHUB_OWNER, GITHUB_REPO, LATEST_RELEASE_API_URL, RELEASES_URL, REPO,
    _version_tuple, check_latest_release,
)
from .controls import SegmentedModeToggle
from .design import LANGUAGES


ISSUES_URL = f"https://github.com/{REPO}/issues"
LICENSE_URL = f"https://github.com/{REPO}/community/license"
TMDB_KEY_URL = "https://www.themoviedb.org/settings/api"
TMDB_SIGNUP_URL = "https://www.themoviedb.org/signup"
TMDB_ATTRIBUTION = (
    "This product uses the TMDB API but is not endorsed or certified by TMDB."
)
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
    "language": LANGUAGES[0],
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
log = logging.getLogger(__name__)


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
            self._schedule_save()
        self.on_change(key, value)

    def update(self, values: Dict[str, object]) -> None:
        """Update related settings as one debounced write."""
        with self._lock:
            self.data.update(values)
            if not self.data.get("remember", True):
                self.data.pop("key", None)
            self._schedule_save()
        for key, value in values.items():
            self.on_change(key, value)

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


class SettingsView(ttk.Frame):
    """Tabbed settings layout and asynchronous validation/update status."""

    _SECTIONS = (
        ("General", "General"),
        ("Formatting", "Formatting"),
        ("Matching", "Matching"),
        ("Files & Safety", "Files & Safety"),
        ("About", "About"),
    )
    _V3_KEY = re.compile(r"^[0-9a-fA-F]{32}$")

    def __init__(
        self, parent, app, store, variables,
    ):
        super().__init__(parent)
        self.app = app
        self.store = store
        self.vars = variables
        self._queue = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._closed = False
        self._key_after = None
        self._key_generation = 0
        self._update_checking = False
        self._poll_id = None
        self._labels = []
        self._preset_cards = {"movie": [], "tv": []}
        self._format_preset_grids = {}
        self._format_grid_columns = {}
        self._syncing_tv_folder = False
        self._styled_entries = []
        self._rich_texts = []
        self._current_section = "General"
        self._wheel_remainder = 0.0
        self._scroll_bindings = []
        self.store.on_error = lambda error: self._queue.put(
            ("save_error", str(error)),
        )
        self._build()
        self._bind_preferences()
        self._show_section("General")
        self._schedule_key_check(800)
        self._poll_id = self.after(100, self._poll)

    def _build(self):
        self._configure_settings_styles()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(self, padding=(14, 16))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.content = ttk.Frame(self, padding=(26, 20))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(
            self.content, background=self.app.pal["bg"],
            highlightthickness=0, bd=0, takefocus=True,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_content = ttk.Frame(self.content_canvas)
        self._scroll_window = self.content_canvas.create_window(
            0, 0, anchor="nw", window=self.scroll_content,
        )
        self.scroll_content.columnconfigure(0, weight=1)
        self.content_canvas.bind(
            "<Configure>", self._resize_scroll_content,
        )
        self.scroll_content.bind(
            "<Configure>", self._update_scroll_region,
        )
        self._bind_scroll_events()
        ttk.Button(
            self.sidebar, text="←  Back", style="Settings.TButton",
            command=self.app.leave_settings,
        ).pack(fill="x", pady=(0, 20))
        self.nav_buttons = {}
        for key, title in self._SECTIONS:
            button = ttk.Button(
                self.sidebar, text=title, style="Settings.TButton",
                command=lambda name=key: (
                    self._show_section(name)
                ),
            )
            button.pack(fill="x", pady=3)
            self.nav_buttons[key] = button
        self.section_frames = {}
        for name, _title in self._SECTIONS:
            frame = ttk.Frame(self.scroll_content)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self.section_frames[name] = frame
        self._build_general(self.section_frames["General"])
        self._build_formatting(self.section_frames["Formatting"])
        self._build_matching(self.section_frames["Matching"])
        self._build_safety(self.section_frames["Files & Safety"])
        self._build_about(self.section_frames["About"])

    def _label(self, parent, text="", muted=False, **kwargs):
        label = ttk.Label(parent, text=text, **kwargs)
        self._labels.append((label, muted))
        label.configure(foreground=(
            self.app.pal["muted"] if muted else self.app.pal["fg"]
        ))
        return label

    def _section_header(self, parent, title, subtitle, bottom_pad=18):
        self._label(parent, title, font="MoviqTitleFont").pack(
            anchor="w",
        )
        self._label(parent, subtitle, muted=True).pack(
            anchor="w", pady=(3, bottom_pad),
        )

    def _group(self, parent, title):
        frame = ttk.LabelFrame(parent, text=title, padding=(14, 10))
        frame.pack(fill="x", pady=(0, 14))
        frame.columnconfigure(1, weight=1)
        return frame

    def _configure_settings_styles(self, palette=None):
        style = ttk.Style(self)
        palette = palette or self.app.pal
        format_switch_style = (
            "Formatting.Switch.TCheckbutton" if self.app.have_sv
            else "Formatting.TCheckbutton"
        )
        style.configure(
            format_switch_style,
            background=palette["preset_surface"],
            foreground=palette["fg"],
            padding=0,
        )
        style.map(
            format_switch_style,
            background=[
                ("disabled", palette["preset_surface"]),
                ("active", palette["preset_surface"]),
            ],
            foreground=[
                ("disabled", palette["muted"]),
                ("!disabled", palette["fg"]),
            ],
        )
        style.configure(
            "Settings.TButton",
            background=palette["btn"],
            foreground=palette["fg"],
            borderwidth=0,
            relief="flat",
            padding=(14, 8),
            font="TkDefaultFont",
        )
        style.map(
            "Settings.TButton",
            background=[
                ("pressed", palette["accent"]),
                ("active", palette["btn_hi"]),
            ],
        )

    def _resize_scroll_content(self, event):
        self.content_canvas.itemconfigure(
            self._scroll_window, width=event.width,
        )

    def _update_scroll_region(self, _event=None):
        self.content_canvas.configure(
            scrollregion=self.content_canvas.bbox("all"),
        )

    def _bind_scroll_events(self):
        bindings = (
            ("<MouseWheel>", self._on_mousewheel),
            ("<Button-4>", self._on_mousewheel_linux),
            ("<Button-5>", self._on_mousewheel_linux),
            ("<Prior>", self._on_scroll_key),
            ("<Next>", self._on_scroll_key),
            ("<Control-Home>", self._on_scroll_key),
            ("<Control-End>", self._on_scroll_key),
            ("<Up>", self._on_scroll_key),
            ("<Down>", self._on_scroll_key),
        )
        for sequence, callback in bindings:
            binding_id = self.bind_all(sequence, callback, add="+")
            self._scroll_bindings.append((sequence, binding_id))

    def _pointer_over_content(self):
        x, y = self.winfo_pointerxy()
        left = self.content_canvas.winfo_rootx()
        top = self.content_canvas.winfo_rooty()
        return (
            self.content_canvas.winfo_ismapped()
            and left <= x < left + self.content_canvas.winfo_width()
            and top <= y < top + self.content_canvas.winfo_height()
        )

    def _on_mousewheel(self, event):
        if not self._pointer_over_content():
            return
        self._wheel_remainder += -event.delta / 30
        units = int(self._wheel_remainder)
        if units:
            self.content_canvas.yview_scroll(units, "units")
            self._wheel_remainder -= units
        return "break"

    def _on_mousewheel_linux(self, event):
        if not self._pointer_over_content():
            return
        self.content_canvas.yview_scroll(
            -1 if event.num == 4 else 1, "units",
        )
        return "break"

    def _on_scroll_key(self, event):
        focus = self.focus_get()
        if not self._is_scroll_content_widget(focus):
            return
        if event.keysym in ("Up", "Down") and isinstance(
            focus, (
                tk.Entry, ttk.Entry, ttk.Combobox, ttk.Scale,
                ttk.Checkbutton, ttk.Radiobutton, tk.Text,
            ),
        ):
            return
        if event.keysym == "Prior":
            self.content_canvas.yview_scroll(-1, "pages")
        elif event.keysym == "Next":
            self.content_canvas.yview_scroll(1, "pages")
        elif event.keysym == "Home":
            self.content_canvas.yview_moveto(0)
        elif event.keysym == "End":
            self.content_canvas.yview_moveto(1)
        elif event.keysym == "Up":
            self.content_canvas.yview_scroll(-1, "units")
        elif event.keysym == "Down":
            self.content_canvas.yview_scroll(1, "units")
        else:
            return
        return "break"

    def _is_scroll_content_widget(self, widget):
        while widget is not None:
            if widget is self.scroll_content:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _row(self, parent, row, title, control, description=""):
        self._label(
            parent, title, font="TkHeadingFont",
        ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        if description:
            self._label(parent, description, muted=True).grid(
                row=row + 1, column=0, sticky="w", padx=(0, 18),
                pady=(0, 7),
            )
            control.grid(
                row=row, column=1, rowspan=2, sticky="e", pady=7,
            )
            return row + 2
        control.grid(row=row, column=1, sticky="e", pady=7)
        return row + 1

    def _switch(self, parent, variable, text="", style=None):
        style = style or (
            "Switch.TCheckbutton" if self.app.have_sv else "TCheckbutton"
        )
        return ttk.Checkbutton(parent, text=text, variable=variable, style=style)

    def make_styled_entry(
        self, parent, textvariable, width=None, show=None,
    ):
        """Create a card-colored entry with a theme-aware focus border."""
        palette = self.app.pal
        wrap = tk.Frame(
            parent,
            background=palette["preset_surface"],
            highlightthickness=1,
            highlightbackground=palette["preset_border"],
            highlightcolor=palette["preset_border"],
        )
        entry_options = {
            "master": wrap,
            "textvariable": textvariable,
            "font": "TkTextFont",
            "background": palette["preset_surface"],
            "readonlybackground": palette["preset_surface"],
            "foreground": palette["fg"],
            "disabledforeground": palette["muted"],
            "disabledbackground": palette["preset_surface"],
            "insertbackground": palette["fg"],
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "takefocus": True,
        }
        if width is not None:
            entry_options["width"] = width
        if show is not None:
            entry_options["show"] = show
        entry = tk.Entry(**entry_options)
        entry.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        wrap.columnconfigure(0, weight=1)
        wrap.bind(
            "<FocusIn>",
            lambda _event, container=wrap: self._style_entry_border(
                container, True,
            ),
        )
        wrap.bind(
            "<FocusOut>",
            lambda _event, container=wrap: self._style_entry_border(
                container, False,
            ),
        )
        entry.bind(
            "<FocusIn>",
            lambda _event, container=wrap: self._style_entry_border(
                container, True,
            ),
        )
        entry.bind(
            "<FocusOut>",
            lambda _event, container=wrap: self._style_entry_border(
                container, False,
            ),
        )
        self._styled_entries.append((wrap, entry))
        return wrap, entry

    def _style_entry_border(self, wrap, focused):
        palette = self.app.pal
        border = palette["accent"] if focused else palette["preset_border"]
        wrap.configure(
            background=palette["preset_surface"],
            highlightbackground=border,
            highlightcolor=border,
        )

    def _recolor_styled_entries(self, palette):
        for wrap, entry in self._styled_entries:
            focused = entry == entry.focus_get()
            border = palette["accent"] if focused else palette["preset_border"]
            wrap.configure(
                background=palette["preset_surface"],
                highlightbackground=border,
                highlightcolor=border,
            )
            entry.configure(
                background=palette["preset_surface"],
                readonlybackground=palette["preset_surface"],
                foreground=palette["fg"],
                disabledforeground=palette["muted"],
                disabledbackground=palette["preset_surface"],
                insertbackground=palette["fg"],
            )

    def _build_general(self, frame):
        self._section_header(
            frame, "General", "Manage your TMDB connection and appearance.",
        )
        group = self._group(frame, "TMDB")
        self.key_status_var = tk.StringVar(value="● Not checked")
        key_row = ttk.Frame(group)
        key_row.grid(row=0, column=1, sticky="e", pady=5)
        self.key_entry_wrap, self.key_entry = self.make_styled_entry(
            key_row, self.vars["key"], width=30, show="•",
        )
        self.key_entry.bind("<<Copy>>", self._copy_api_key, add="+")
        self.key_entry_wrap.pack(side="left")
        self.key_visibility_button = ttk.Button(
            key_row, text="Show", style="Settings.TButton",
            command=self._toggle_key_visibility,
        )
        self.key_visibility_button.pack(side="left", padx=(6, 0))
        ttk.Button(
            key_row, text="Test key", style="Settings.TButton",
            command=self.test_key,
        ).pack(side="left", padx=(6, 0))
        self._label(
            group, "TMDB API Key",
            font="TkHeadingFont",
        ).grid(
            row=0, column=0, sticky="w", padx=(0, 18), pady=5,
        )
        self.key_help_text = self._build_api_key_help(group)
        self.key_status_label = self._label(
            group, textvariable=self.key_status_var, muted=True,
        )
        self._set_key_status("Not checked")
        self.key_status_label.grid(row=1, column=1, sticky="e", pady=(0, 5))
        self.key_help_text.grid(
            row=2, column=0, columnspan=2, sticky="ew",
            padx=(0, 18), pady=(0, 8),
        )
        ttk.Separator(group).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(2, 8),
        )
        self._row(
            group, 4, "Remember key",
            self._switch(group, self.vars["remember"]),
            "When off, the API key is kept only for this session.",
        )

        appearance = self._group(frame, "Appearance")
        theme_controls = ttk.Frame(appearance)
        for text, value in (
            ("Light", "light"), ("Dark", "dark"), ("System", "system"),
        ):
            ttk.Radiobutton(
                theme_controls, text=text, value=value,
                variable=self.vars["theme"],
            ).pack(side="left", padx=(0, 12))
        self._row(appearance, 0, "Theme", theme_controls)

    def _build_api_key_help(self, parent):
        text = tk.Text(
            parent,
            width=1,
            height=1,
            wrap="word",
            font="TkDefaultFont",
            background=self.app.pal["card"],
            foreground=self.app.pal["muted"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            takefocus=False,
            exportselection=False,
        )
        text.insert("end", "Create a free account on ")
        text.insert("end", "TMDB", ("tmdb_link",))
        text.insert("end", " \u2192 open ")
        text.insert("end", "Settings", ("bold",))
        text.insert("end", " \u2192 go to the ")
        text.insert("end", "API", ("api_link",))
        text.insert("end", " section \u2192 copy your ")
        text.insert("end", "API Key", ("bold",))
        text.insert("end", " and paste it here.")
        text.tag_configure("bold", font="TkHeadingFont")
        text.tag_configure(
            "bold",
            foreground=self.app.pal["muted"],
        )
        for tag in ("tmdb_link", "api_link"):
            text.tag_configure(
                tag,
                foreground=self.app.pal["accent"],
            )
            text.tag_bind(
                tag,
                "<Enter>",
                lambda _event, link_tag=tag: (
                    self._set_api_help_link_hover(text, link_tag, True)
                ),
            )
            text.tag_bind(
                tag,
                "<Leave>",
                lambda _event, link_tag=tag: (
                    self._set_api_help_link_hover(text, link_tag, False)
                ),
            )
        text.bind(
            "<Button-1>",
            lambda event, widget=text: self._open_api_help_link(
                event, widget,
            ),
        )
        text.bind(
            "<Configure>",
            lambda _event, widget=text: self._resize_api_key_help(widget),
        )
        text.configure(state="disabled", cursor="arrow")
        self._rich_texts.append(text)
        return text

    @staticmethod
    def _set_api_help_link_hover(text, tag, hovered):
        text.tag_configure(tag, underline=hovered)
        text.configure(cursor="hand2" if hovered else "arrow")

    @staticmethod
    def _open_api_help_link(event, text):
        index = text.index(f"@{event.x},{event.y}")
        tags = text.tag_names(index)
        urls = {
            "tmdb_link": TMDB_SIGNUP_URL,
            "api_link": TMDB_KEY_URL,
        }
        for tag, url in urls.items():
            if tag in tags:
                webbrowser.open(url)
                break
        return "break"

    @staticmethod
    def _resize_api_key_help(text):
        display_lines = text.count("1.0", "end-1c", "displaylines")
        if display_lines:
            text.configure(height=display_lines[0])

    def _build_formatting(self, frame):
        header = ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 6))
        self._label(
            header, "Formatting", font="MoviqTitleFont",
        ).grid(row=0, column=0, sticky="w")
        self._label(
            header,
            "Choose folder and file names for movies and TV episodes.",
            muted=True,
        ).grid(row=1, column=0, sticky="w")
        header.columnconfigure(0, weight=1)
        self.format_selector = SegmentedModeToggle(
            header, "movie", self._select_format_tab,
            font="TkDefaultFont", ui_scale=self.app.UI_SCALE,
        )
        self.format_selector.grid(
            row=0, column=1, rowspan=2, sticky="e", padx=(16, 0),
        )
        pages = ttk.Frame(frame)
        pages.pack(fill="both", expand=True)
        pages.columnconfigure(0, weight=1)
        pages.rowconfigure(0, weight=1)
        self.movie_tab = ttk.Frame(pages, padding=(0, 2))
        self.tv_tab = ttk.Frame(pages, padding=(0, 2))
        self.movie_tab.grid(row=0, column=0, sticky="nsew")
        self.tv_tab.grid(row=0, column=0, sticky="nsew")
        self.tv_tab.grid_remove()
        self._format_pages = {"movie": self.movie_tab, "tv": self.tv_tab}
        self._format_bodies = {}
        self._format_body_modes = {}
        self._format_fields = {"movie": [], "tv": []}
        self._format_entries = {"movie": {}, "tv": {}}
        self._format_token_buttons = {"movie": [], "tv": []}
        self._format_token_rows = {}
        self._format_folder_toggles = {"movie": {}, "tv": {}}
        self._format_panels = {}
        self._format_right_panes = {}
        self._format_header_hints = {}
        self._format_hint_labels = {}
        self._format_panel_labels = {}
        self._format_panel_surfaces = {}
        self._format_preview_before = {}
        self._format_preview_after = {}
        self._format_last_entry = {}
        self._updating_format = set()
        self._current_format_id = {
            "movie": self.vars["movie_preset_id"].get(),
            "tv": self.vars["tv_preset_id"].get(),
        }
        self._custom_initialized = {
            "movie": self._current_format_id["movie"] == CUSTOM_ID
            or any(self.store.data.get(key) for key in (
                "movie_custom_folder_pattern", "movie_custom_file_pattern",
            )),
            "tv": self._current_format_id["tv"] == CUSTOM_ID
            or any(self.store.data.get(key) for key in (
                "tv_custom_show_folder_pattern",
                "tv_custom_season_folder_pattern",
                "tv_custom_file_pattern",
            )),
        }
        self._build_format_tab(self.movie_tab, "movie", MOVIE_FORMATS)
        self._build_format_tab(self.tv_tab, "tv", TV_FORMATS)
        for kind, body in self._format_bodies.items():
            body.bind(
                "<Configure>",
                lambda event, current=kind: self._layout_format_body(
                    current, event.width,
                ),
                add="+",
            )
        self.after_idle(
            lambda: self._layout_format_body(
                "movie", self.movie_tab.winfo_width(),
            ),
        )
        self._set_format_selector_theme(self.app.pal)

    def _select_format_tab(self, kind):
        for current_kind, records in self._preset_cards.items():
            for record in records:
                record["hover"] = False
            self._refresh_preset_cards(current_kind)
        for name, page in self._format_pages.items():
            if name == kind:
                page.grid()
            else:
                page.grid_remove()
        self.format_selector.set_mode(kind)

    def _set_format_selector_theme(self, palette):
        self.format_selector.set_theme(
            background=palette["header_bg"],
            track=palette["header_bg"],
            border=palette["header_accent"],
            active=palette["header_accent"],
            foreground=palette["fg"],
            muted=palette["header_muted"],
            accent_foreground=palette["accent_text"],
            canvas_background=palette["header_bg"],
        )
        self.format_selector.set_mode(
            self.format_selector.mode, animate=False,
        )

    def _build_format_tab(self, parent, kind, presets):
        body = ttk.Frame(parent)
        body.pack(fill="both", expand=True)
        self._format_bodies[kind] = body
        body.columnconfigure(0, weight=3, uniform=f"{kind}_format_panes")
        body.columnconfigure(1, weight=2, uniform=f"{kind}_format_panes")
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.columnconfigure(0, weight=1)
        right_pane = ttk.Frame(body)
        right_pane.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_pane.columnconfigure(0, weight=1)
        right_pane.rowconfigure(1, weight=1)
        self._format_right_panes[kind] = right_pane
        right_header = ttk.Frame(right_pane)
        right_header.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        right_header.columnconfigure(0, weight=1)
        self._label(
            right_header, "Patterns", font="TkHeadingFont",
        ).grid(row=0, column=0, sticky="w")
        hint = tk.Label(
            right_header, text="", font="TkTextFont",
            background=self.app.pal["bg"],
            foreground=self.app.pal["muted"],
        )
        hint.grid(row=0, column=1, sticky="e")
        self._format_header_hints[kind] = hint
        right_panel = tk.Frame(
            right_pane,
            background=self.app.pal["preset_surface"],
            highlightthickness=1,
            highlightbackground=self.app.pal["preset_border"],
            highlightcolor=self.app.pal["preset_border"],
            padx=12,
            pady=10,
        )
        right_panel.grid(row=1, column=0, sticky="nsew")
        right_panel.columnconfigure(0, weight=1)
        self._format_panels[kind] = right_panel
        self._format_panel_labels[kind] = {
            "primary": [], "muted": [], "accent": [],
        }
        self._format_panel_surfaces[kind] = [right_panel]
        preset_var = self.vars[f"{kind}_preset_id"]
        setattr(self, f"{kind}_preset_var", preset_var)
        self._label(
            left, "Preset", font="TkHeadingFont",
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        preset_grid = ttk.Frame(left)
        preset_grid.grid(row=1, column=0, sticky="new")
        self._format_preset_grids[kind] = preset_grid
        self._preset_cards[kind] = [
            self._make_format_preset_card(preset_grid, kind, preset)
            for preset in presets
        ]
        self._preset_cards[kind].append(
            self._make_format_preset_card(
                preset_grid, kind,
                ("custom", "Custom", "", "") if kind == "movie"
                else ("custom", "Custom", "", "", "", ""),
                custom=True,
            ),
        )
        self._layout_format_presets(kind, 2)
        self._format_hint_labels[kind] = hint
        field_definitions = (
            (
                ("Folder pattern", "movie_folder_pattern",
                 "movie_create_folder"),
                ("File pattern", "movie_file_pattern", None),
            ) if kind == "movie" else (
                ("Show folder pattern", "tv_show_folder_pattern",
                 "tv_create_show_folder"),
                ("Season folder pattern", "tv_season_folder_pattern",
                 "tv_create_season_folder"),
                ("File pattern", "tv_file_pattern", None),
            )
        )
        self._format_entries[kind] = {}
        for index, (label, variable_key, create_key) in enumerate(field_definitions):
            field_row_index = index * 2
            field_variable = self.vars[variable_key]
            field_row = tk.Frame(
                right_panel, background=self.app.pal["preset_surface"],
            )
            field_row.grid(
                row=field_row_index, column=0, sticky="ew", pady=(2, 0),
            )
            self._format_panel_surfaces[kind].append(field_row)
            field_row.columnconfigure(0, weight=1)
            field_label = tk.Label(
                field_row, text=label, font="TkHeadingFont",
                background=self.app.pal["preset_surface"],
                foreground=self.app.pal["fg"],
            )
            field_label.grid(row=0, column=0, sticky="w")
            self._format_panel_labels[kind]["primary"].append(field_label)
            create_variable = (
                self.vars[create_key] if create_key else None
            )
            if create_variable is not None:
                checkbox = self._switch(
                    field_row, create_variable, "Create folder",
                    style=(
                        "Formatting.Switch.TCheckbutton"
                        if self.app.have_sv else "Formatting.TCheckbutton"
                    ),
                )
                checkbox.grid(row=0, column=1, sticky="e", padx=(8, 0))
                self._format_folder_toggles[kind][create_key] = checkbox
            entry_wrap, entry = self.make_styled_entry(
                right_panel, field_variable,
            )
            entry.configure(font=("Consolas", 9))
            entry.grid_configure(padx=7, pady=3)
            entry_wrap.grid(
                row=field_row_index + 1, column=0, sticky="ew",
                pady=(1, 3),
            )
            self._format_entries[kind][variable_key] = entry
            entry.bind(
                "<FocusIn>",
                lambda _event, k=kind, current=entry: (
                    self._format_last_entry.__setitem__(k, current)
                ),
                add="+",
            )
            self._format_fields[kind].append((
                variable_key, field_variable, create_key, create_variable,
            ))

        self._format_last_entry[kind] = self._format_entries[kind][
            "movie_file_pattern" if kind == "movie" else "tv_file_pattern"
        ]
        token_row = len(field_definitions) * 2
        token_bar = ttk.Frame(right_panel)
        token_bar.grid(row=token_row, column=0, sticky="w", pady=(2, 5))
        self._format_token_rows[kind] = token_bar
        tokens = (
            ("{title}", "{year}", "{id}") if kind == "movie" else (
                "{title}", "{year}", "{id}", "{season}", "{season:00}",
                "{episode:00}", "{episode_title}",
            )
        )
        for token in tokens:
            button = ttk.Button(
                token_bar, text=token, style="Settings.TButton",
                command=lambda text=token, k=kind: (
                    self._insert_format_token(k, text)
                ),
            )
            button.pack(side="left", padx=(0, 4))
            self._format_token_buttons[kind].append(button)
        ttk.Separator(right_panel, orient="horizontal").grid(
            row=token_row + 1, column=0, sticky="ew", pady=(2, 7),
        )
        preview_heading = tk.Label(
            right_panel, text="Preview", font="TkHeadingFont",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["fg"],
        )
        preview_heading.grid(
            row=token_row + 2, column=0, sticky="w", pady=(0, 3),
        )
        self._format_panel_labels[kind]["primary"].append(preview_heading)
        preview = tk.Frame(
            right_panel, background=self.app.pal["preset_surface"],
        )
        preview.grid(row=token_row + 3, column=0, sticky="new")
        self._format_panel_surfaces[kind].extend((preview,))
        before_caption = tk.Label(
            preview, text="Before:", font="TkTextFont",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["muted"],
        )
        before_caption.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        self._format_panel_labels[kind]["muted"].append(before_caption)
        before_label = tk.Label(
            preview, text="", font=("Consolas", 9), anchor="w",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["muted"], justify="left",
        )
        before_label.grid(row=0, column=1, sticky="ew")
        self._format_panel_labels[kind]["muted"].append(before_label)
        after_caption = tk.Label(
            preview, text="After:", font="TkTextFont",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["muted"],
        )
        after_caption.grid(
            row=1, column=0, sticky="nw", padx=(0, 6), pady=(3, 0),
        )
        self._format_panel_labels[kind]["muted"].append(after_caption)
        after_label = tk.Label(
            preview, text="", font=("Consolas", 9), anchor="w",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["accent"], justify="left",
        )
        after_label.grid(row=1, column=1, sticky="ew", pady=(3, 0))
        preview.columnconfigure(1, weight=1)
        preview.bind(
            "<Configure>",
            lambda event, label=after_label: label.configure(
                wraplength=max(120, event.width - 80),
            ),
            add="+",
        )
        self._format_preview_before[kind] = before_label
        self._format_preview_after[kind] = after_label
        self._format_panel_labels[kind]["accent"].append(after_label)
        preview_var = tk.StringVar(value="")
        setattr(self, f"{kind}_preview_var", preview_var)
        for _pattern_key, field_variable, _create_key, create_variable in self._format_fields[kind]:
            field_variable.trace_add(
                "write",
                lambda *_args, k=kind: self._formatting_changed(k),
            )
            if create_variable is not None:
                create_variable.trace_add(
                    "write",
                    lambda *_args, k=kind: self._formatting_changed(k),
                )
        self._update_pattern_editing(kind)
        self._refresh_preset_cards(kind)
        self._formatting_changed(kind)

    def _layout_format_body(self, kind, width):
        stacked = width < 1000
        if self._format_body_modes.get(kind) == stacked:
            return
        self._format_body_modes[kind] = stacked
        body = self._format_bodies[kind]
        if stacked:
            body.columnconfigure(0, weight=1, uniform="")
            body.columnconfigure(1, weight=0, uniform="")
            body.rowconfigure(0, weight=0)
            body.rowconfigure(1, weight=1)
            self._format_preset_grids[kind].master.grid_configure(
                row=0, column=0, sticky="new", padx=0, pady=(0, 8),
            )
            self._format_right_panes[kind].grid_configure(
                row=1, column=0, sticky="new", padx=0, pady=0,
            )
        else:
            body.columnconfigure(0, weight=3, uniform=f"{kind}_format_panes")
            body.columnconfigure(1, weight=2, uniform=f"{kind}_format_panes")
            body.rowconfigure(0, weight=1)
            body.rowconfigure(1, weight=0)
            self._format_preset_grids[kind].master.grid_configure(
                row=0, column=0, sticky="nsew", padx=(0, 8), pady=0,
            )
            self._format_right_panes[kind].grid_configure(
                row=0, column=1, sticky="nsew", padx=(8, 0), pady=0,
            )

    def _recolor_format_panel(self, kind, palette):
        surface = palette["preset_surface"]
        for widget in self._format_panel_surfaces[kind]:
            widget.configure(background=surface)
        self._format_panels[kind].configure(
            highlightbackground=palette["preset_border"],
            highlightcolor=palette["preset_border"],
        )
        for widget in self._format_panel_labels[kind]["primary"]:
            widget.configure(background=surface, foreground=palette["fg"])
        for widget in self._format_panel_labels[kind]["muted"]:
            widget.configure(background=surface, foreground=palette["muted"])
        for widget in self._format_panel_labels[kind]["accent"]:
            widget.configure(background=surface, foreground=palette["accent"])
        self._format_hint_labels[kind].configure(
            background=palette["bg"], foreground=palette["muted"],
        )
        self._format_preview_after[kind].configure(
            background=surface, foreground=palette["accent"],
        )

    def _make_format_preset_card(
        self, parent, kind, preset, custom=False,
    ):
        card = tk.Frame(
            parent, bd=0,
            highlightthickness=1, takefocus=True,
        )
        content = tk.Frame(card, bd=0, highlightthickness=0)
        content.pack(fill="both", expand=True, padx=10, pady=8)
        title_row = tk.Frame(content, bd=0, highlightthickness=0)
        title_row.grid(row=0, column=0, columnspan=2, sticky="ew")
        title_row.columnconfigure(1, weight=1)
        indicator = tk.Label(title_row, text="", font="TkHeadingFont")
        indicator.grid(row=0, column=0, sticky="w", padx=(0, 6))
        title = tk.Label(
            title_row, text=preset[1] if not custom else "Custom",
            font="TkHeadingFont", anchor="w",
        )
        title.grid(row=0, column=1, sticky="w")
        tag = None
        if kind == "tv" and not custom:
            tag = tk.Label(
                title_row, text=preset[5], font="TkTextFont",
                padx=5, pady=2, highlightthickness=1,
            )
            tag.grid(row=0, column=2, sticky="e", padx=(6, 0))
        value_rows = []
        if custom:
            preview = tk.Label(
                content, text="Build your own folder and file names",
                font="TkTextFont", anchor="w", justify="left",
            )
            preview.grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
            value_rows.append(preview)
        else:
            labels = ("Folder", "File") if kind == "movie" else (
                "Show", "Season", "File",
            )
            patterns = (
                preset[2:4] if kind == "movie" else preset[2:5]
            )
            for row_index, (label_text, pattern) in enumerate(
                zip(labels, patterns), 1,
            ):
                label = tk.Label(
                    content, text=label_text, font="TkTextFont",
                    anchor="w", width=6,
                )
                label.grid(row=row_index, column=0, sticky="nw", pady=(2, 0))
                value = tk.Label(
                    content, text="", font=("Consolas", 9),
                    anchor="w", justify="left",
                )
                value.grid(row=row_index, column=1, sticky="new", pady=(2, 0))
                value_rows.append((label, value, pattern))
            content.columnconfigure(1, weight=1)
        record = {
            "card": card,
            "text_block": content,
            "title_row": title_row,
            "indicator": indicator,
            "title": title,
            "example": preview if custom else None,
            "value_rows": value_rows,
            "tag": tag,
            "preset": preset,
            "selected": preset[0],
            "hover": False,
            "custom": custom,
        }
        bound_widgets = [card, title_row, indicator, content, title]
        if custom:
            bound_widgets.append(preview)
        if not custom:
            for label, value, _pattern in value_rows:
                bound_widgets.extend((label, value))
        for widget in bound_widgets:
            widget.bind(
                "<Button-1>",
                lambda _event, k=kind, preset_id=preset[0]: (
                    self._select_format_preset(k, preset_id)
                ),
            )
            widget.bind(
                "<Enter>",
                lambda _event, item=record: self._schedule_preset_hover(item),
            )
            widget.bind(
                "<Leave>",
                lambda _event, item=record: self._schedule_preset_hover(item),
            )
        if tag is not None:
            tag.bind(
                "<Enter>",
                lambda _event, item=record: self._schedule_preset_hover(item),
            )
            tag.bind(
                "<Leave>",
                lambda _event, item=record: self._schedule_preset_hover(item),
            )
            tag.bind(
                "<Button-1>",
                lambda _event, k=kind, preset_id=preset[0]: (
                    self._select_format_preset(k, preset_id)
                ),
            )
        card.bind(
            "<Return>",
            lambda _event, k=kind, preset_id=preset[0]: (
                self._select_format_preset(k, preset_id)
            ),
        )
        card.bind(
            "<space>",
            lambda _event, k=kind, preset_id=preset[0]: (
                self._select_format_preset(k, preset_id)
            ),
        )
        return record

    def _layout_format_presets(self, kind, width):
        columns = 2
        if self._format_grid_columns.get(kind) == columns:
            return

        grid = self._format_preset_grids[kind]
        for column in range(columns):
            grid.columnconfigure(
                column, weight=1, uniform="format_presets",
            )
        self._format_grid_columns[kind] = columns
        for index, record in enumerate(self._preset_cards[kind]):
            record["card"].grid_configure(
                row=index // columns,
                column=index % columns,
                sticky="nsew",
                padx=(0, 3) if index % columns == 0 else (3, 0),
                pady=3,
            )
            record["card"].bind(
                "<Configure>",
                lambda event, item=record: self._resize_preset_values(
                    item, event.width,
                ),
                add="+",
            )

    @staticmethod
    def _resize_preset_values(record, width):
        if record["custom"]:
            return
        wraplength = max(80, width - 100)
        for _label, value, _pattern in record["value_rows"]:
            value.configure(wraplength=wraplength)

    def _choose_preset(self, kind, pattern, variable, presets):
        """Compatibility helper for settings migration tests."""
        getattr(self, f"{kind}_preset_var").set(pattern)
        variable.set(pattern)

    def _choose_custom(self, kind):
        self._select_format_preset(kind, CUSTOM_ID)

    def _update_pattern_editing(self, kind):
        custom = self._current_format_id[kind] == CUSTOM_ID
        self._format_hint_labels[kind].configure(
            text="Editable" if custom else "Choose Custom to edit",
        )
        if custom:
            self._format_token_rows[kind].grid()
        else:
            self._format_token_rows[kind].grid_remove()
        for toggle in self._format_folder_toggles[kind].values():
            toggle.configure(state="normal" if custom else "disabled")
        for pattern_key, _variable, create_key, create_variable in (
            self._format_fields[kind]
        ):
            entry = self._format_entries[kind][pattern_key]
            enabled = create_key is None or create_variable.get()
            entry.configure(
                state=(
                    "disabled" if not enabled
                    else "normal" if custom else "readonly"
                ),
                foreground=(
                    self.app.pal["fg"] if custom
                    else self.app.pal["muted"]
                ),
            )
        self._format_entries[kind][
            "movie_file_pattern" if kind == "movie" else "tv_file_pattern"
        ].configure(
            state="normal" if custom else "readonly",
            foreground=(
                self.app.pal["fg"] if custom else self.app.pal["muted"]
            ),
        )
        for button in self._format_token_buttons[kind]:
            button.configure(state="normal" if custom else "disabled")

    def _schedule_preset_hover(self, record):
        self.after_idle(lambda item=record: self._update_preset_hover(item))

    def _update_preset_hover(self, record):
        card = record["card"]
        pointer_x, pointer_y = self.winfo_pointerxy()
        left, top = card.winfo_rootx(), card.winfo_rooty()
        hovered = (
            left <= pointer_x < left + card.winfo_width()
            and top <= pointer_y < top + card.winfo_height()
        )
        self._preset_hover(record, hovered)

    def _preset_hover(self, record, hovered):
        if record["hover"] == hovered:
            return
        record["hover"] = hovered
        self._refresh_preset_cards(
            "movie" if record in self._preset_cards["movie"] else "tv",
        )

    def _refresh_preset_cards(self, kind):
        palette = self.app.pal
        selected = self._current_format_id[kind]
        for record in self._preset_cards[kind]:
            is_selected = selected == record["selected"]
            background = (
                palette["preset_selected"]
                if is_selected
                else palette["preset_hover"]
                if record["hover"]
                else palette["preset_surface"]
            )
            border = (
                palette["accent"] if is_selected else palette["preset_border"]
            )
            foreground = palette["fg"]
            muted = palette["muted"]
            record["card"].configure(
                background=background,
                highlightbackground=border,
                highlightcolor=border,
                cursor="hand2",
            )
            record["text_block"].configure(background=background)
            record["title_row"].configure(background=background)
            record["indicator"].configure(
                background=background, foreground=palette["accent"] if is_selected
                else muted, text="●" if is_selected else "○",
                cursor="hand2",
            )
            record["title"].configure(
                background=background, foreground=foreground, cursor="hand2",
            )
            if record["example"] is not None:
                record["example"].configure(
                    text="Build your own folder and file names",
                )
                record["example"].configure(
                    background=background, foreground=muted, cursor="hand2",
                )
            else:
                values = MOVIE_SAMPLE if kind == "movie" else TV_SAMPLE
                for label, value, pattern in record["value_rows"]:
                    rendered = format_pattern(pattern, values)
                    if label.cget("text") == "File":
                        rendered += ".mkv"
                    label.configure(
                        background=background, foreground=muted,
                        cursor="hand2",
                    )
                    value.configure(
                        text=rendered,
                        background=background, foreground=foreground,
                        cursor="hand2",
                    )
            if record["tag"] is not None:
                record["tag"].configure(
                    background=background,
                    foreground=palette["accent"],
                    highlightbackground=palette["accent"],
                    highlightcolor=palette["accent"],
                    cursor="hand2",
                )

    @staticmethod
    def _insert_token(entry, token):
        entry.insert(entry.index(tk.INSERT), token)
        entry.focus_set()

    def _insert_format_token(self, kind, token):
        entry = self._format_last_entry[kind]
        if entry.cget("state") == "disabled":
            entry = self._format_entries[kind][
                "movie_file_pattern" if kind == "movie" else "tv_file_pattern"
            ]
            self._format_last_entry[kind] = entry
        self._insert_token(entry, token)

    def _select_format_preset(self, kind, preset_id):
        if preset_id == self._current_format_id[kind]:
            return
        fields = self._format_fields[kind]
        old_id = self._current_format_id[kind]
        if old_id == CUSTOM_ID:
            self._save_custom_patterns(kind)
        if preset_id == CUSTOM_ID:
            if self._custom_initialized[kind]:
                patterns = self._custom_patterns(kind)
            else:
                patterns = {
                    key: variable.get()
                    for key, variable, _create_key, _create_var in fields
                }
                file_key = (
                    "movie_file_pattern" if kind == "movie"
                    else "tv_file_pattern"
                )
                patterns[file_key] = self.vars[file_key].get()
                self._custom_initialized[kind] = True
        else:
            preset = self._find_format_preset(kind, preset_id)
            patterns = self._preset_patterns(kind, preset)
        self._current_format_id[kind] = preset_id
        self.vars[f"{kind}_preset_id"].set(preset_id)
        self._updating_format.add(kind)
        try:
            for key, value in patterns.items():
                self.vars[key].set(value)
        finally:
            self._updating_format.discard(kind)
        if preset_id == CUSTOM_ID:
            self._save_custom_patterns(kind)
        self._update_pattern_editing(kind)
        self._refresh_preset_cards(kind)
        self._formatting_changed(kind)
        if preset_id == CUSTOM_ID:
            self._format_last_entry[kind].focus_set()

    def _find_format_preset(self, kind, preset_id):
        presets = MOVIE_FORMATS if kind == "movie" else TV_FORMATS
        return next(
            (preset for preset in presets if preset[0] == preset_id),
            next(preset for preset in presets if preset[0] == (
                MOVIE_DEFAULT_ID if kind == "movie" else TV_DEFAULT_ID
            )),
        )

    @staticmethod
    def _preset_patterns(kind, preset):
        if kind == "movie":
            return {
                "movie_folder_pattern": preset[2],
                "movie_file_pattern": preset[3],
            }
        return {
            "tv_show_folder_pattern": preset[2],
            "tv_season_folder_pattern": preset[3],
            "tv_file_pattern": preset[4],
        }

    def _custom_patterns(self, kind):
        suffixes = (
            ("folder_pattern", "file_pattern") if kind == "movie"
            else (
                "show_folder_pattern", "season_folder_pattern",
                "file_pattern",
            )
        )
        return {
            f"{kind}_{suffix}": self.store.data.get(
                f"{kind}_custom_{suffix}", "",
            )
            for suffix in suffixes
        }

    def _save_custom_patterns(self, kind):
        keys = (
            ("movie_folder_pattern", "movie_file_pattern")
            if kind == "movie" else (
                "tv_show_folder_pattern", "tv_season_folder_pattern",
                "tv_file_pattern",
            )
        )
        values = {
            f"{kind}_custom_{key.removeprefix(kind + '_')}":
                self.vars[key].get()
            for key in keys
        }
        self.store.update(values)

    def _formatting_changed(self, kind):
        if kind in self._updating_format:
            return
        self._update_pattern_editing(kind)
        self._refresh_preset_cards(kind)
        values = self._formatting_values(kind)
        before, components = self._format_preview_components(kind, values)
        tree = []
        for index, component in enumerate(components):
            connector = "" if index == 0 else (
                "  " * (index - 1) + "└ "
            )
            tree.append(connector + component)
        after = "\n".join(tree)
        self._format_preview_before[kind].configure(text=before)
        self._format_preview_after[kind].configure(text=after)
        getattr(self, f"{kind}_preview_var").set(
            f"Before: {before}\nAfter: {after}",
        )
        if kind == "tv":
            self._syncing_tv_folder = True
            try:
                self.vars["tv_folder"].set(
                    self.vars["tv_create_show_folder"].get()
                    and self.vars["tv_create_season_folder"].get()
                )
            finally:
                self._syncing_tv_folder = False
        if self._current_format_id[kind] == CUSTOM_ID:
            self._save_custom_patterns(kind)
        self._save_format_settings(kind)

    def _formatting_values(self, kind):
        return {
            key: self.vars[key].get()
            for key, _variable, _create_key, _create_var
            in self._format_fields[kind]
        } | {
            (
                "movie_file_pattern" if kind == "movie"
                else "tv_file_pattern"
            ): self.vars[
                "movie_file_pattern" if kind == "movie"
                else "tv_file_pattern"
            ].get(),
        }

    def _save_format_settings(self, kind):
        values = {f"{kind}_preset_id": self._current_format_id[kind]}
        for key, _variable, create_key, create_variable in self._format_fields[kind]:
            values[key] = self.vars[key].get()
            if create_key and create_variable is not None:
                values[create_key] = create_variable.get()
        file_key = (
            "movie_file_pattern" if kind == "movie" else "tv_file_pattern"
        )
        values[file_key] = self.vars[file_key].get()
        values["name_template" if kind == "movie" else "tv_template"] = (
            self.vars[file_key].get()
        )
        if kind == "movie":
            values["movie_folder"] = self.vars["movie_create_folder"].get()
        else:
            values["tv_folder"] = (
                self.vars["tv_create_show_folder"].get()
                and self.vars["tv_create_season_folder"].get()
            )
        self.store.update(values)

    def _format_preview_components(self, kind, patterns):
        values = MOVIE_SAMPLE if kind == "movie" else TV_SAMPLE
        components = []
        if kind == "movie":
            if self.vars["movie_create_folder"].get():
                components.append(format_pattern(
                    patterns["movie_folder_pattern"], values,
                    "{title} ({year})",
                ))
            file_pattern = patterns["movie_file_pattern"]
            file_name = format_pattern(
                file_pattern, values, "{title} ({year})",
            )
            before = "Inception.2010.mkv"
        else:
            if self.vars["tv_create_show_folder"].get():
                components.append(format_pattern(
                    patterns["tv_show_folder_pattern"], values, "{title}",
                ))
            if self.vars["tv_create_season_folder"].get():
                components.append(format_pattern(
                    patterns["tv_season_folder_pattern"], values,
                    "Season {season:00}",
                ))
            file_name = format_pattern(
                patterns["tv_file_pattern"], values,
                "{title} - S{season:00}E{episode:00} - {episode_title}",
            )
            before = "Breaking.Bad.S01E01.mkv"
        components.append(file_name + ".mkv")
        return before, components

    def _format_output_preview(self, kind, patterns):
        before, components = self._format_preview_components(kind, patterns)
        after = "\\".join(components)
        return f"Before: {before}\nAfter: {after}"

    def _format_preview(self, kind, pattern, sample, warning):
        if warning and not warning.startswith("Warning:"):
            return f"Preview unavailable — {warning}"
        file_name = render_pattern(pattern, sample)
        suffix = ".mkv"
        if kind == "movie":
            folder = (
                f"{file_name}\\"
                if self.vars["movie_folder"].get() else ""
            )
            result = f"{folder}{file_name}{suffix}"
            before = "Inception.2010.mkv"
        else:
            folder = (
                f"{sample['title']}\\Season {sample['season']:02d}\\"
                if self.vars["tv_folder"].get() else ""
            )
            result = f"{folder}{file_name}{suffix}"
            before = "Breaking.Bad.S01E01.mkv"
        return f"Before: {before}   →   After: {result}"

    def _build_matching(self, frame):
        self._section_header(
            frame, "Matching", "Control automatic matching and acceptance.",
        )
        group = self._group(frame, "Automatic matching")
        row = self._row(
            group, 0, "Auto-match new files",
            self._switch(group, self.vars["auto_match"]),
        )
        scale_wrap = ttk.Frame(group)
        scale = ttk.Scale(
            scale_wrap, from_=50, to=100, variable=self.vars["threshold"],
            command=self._threshold_moved, length=300,
        )
        scale.pack(side="left")
        self.threshold_label_var = tk.StringVar()
        self._label(
            scale_wrap, textvariable=self.threshold_label_var,
        ).pack(side="left", padx=10)
        self.threshold_hint_var = tk.StringVar()
        self._label(
            group, textvariable=self.threshold_hint_var, muted=True,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        scale_wrap.grid(row=row - 1, column=1, sticky="e", pady=7)
        self.vars["threshold"].trace_add(
            "write", lambda *_args: self._update_threshold_hint(),
        )
        self._update_threshold_hint()

    def _threshold_moved(self, value):
        snapped = min(100, max(50, int(round(float(value) / 5) * 5)))
        if self.vars["threshold"].get() != snapped:
            self.vars["threshold"].set(snapped)

    def _update_threshold_hint(self):
        threshold = min(
            100, max(50, int(round(self.vars["threshold"].get() / 5) * 5)),
        )
        if self.vars["threshold"].get() != threshold:
            self.vars["threshold"].set(threshold)
        self.threshold_label_var.set(f"{threshold}%")
        if threshold >= 90:
            hint = "Higher thresholds are stricter and send fewer matches to review."
        elif threshold >= 75:
            hint = "Balanced setting: uncertain matches are sent for review."
        else:
            hint = "Lower thresholds accept more matches automatically."
        self.threshold_hint_var.set(hint)

    def _build_safety(self, frame):
        self._section_header(
            frame, "Files & Safety", "Choose how file changes are confirmed and recorded.",
        )
        group = self._group(frame, "File operations")
        for index, (title, key, description) in enumerate((
            ("Confirm before renaming", "confirm_apply", "Ask before applying planned file changes."),
            ("Put each movie in its own folder", "movie_folder", "Create a separate folder for every movie."),
            ("Rename subtitles with the video", "rename_subtitles", "Keep attached subtitle names in sync with the video."),
        )):
            self._row(
                group, index * 2, title,
                self._switch(group, self.vars[key]), description,
            )
        self._label(group, "Library destination").grid(
            row=6, column=0, sticky="w", pady=(8, 4),
        )
        mode_row = ttk.Frame(group)
        mode_row.grid(row=6, column=1, sticky="w", pady=(8, 4))
        for label, value in (
            ("Rename in place", "in_place"),
            ("Move to library", "library"),
        ):
            ttk.Radiobutton(
                mode_row, text=label, value=value,
                variable=self.vars["library_mode"],
            ).pack(side="left", padx=(0, 14))
        self.library_paths_frame = ttk.Frame(group)
        self.library_paths_frame.grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(4, 8),
        )
        self.library_paths_frame.columnconfigure(1, weight=1)
        self._library_path_row(
            self.library_paths_frame, 0, "Movies library folder",
            self.vars["movies_library"], "movies_library",
        )
        self._library_path_row(
            self.library_paths_frame, 1, "Shows library folder",
            self.vars["tv_library"], "tv_library",
        )
        self.vars["library_mode"].trace_add(
            "write", lambda *_args: self._show_library_paths(),
        )
        self._show_library_paths()
    def _library_path_row(self, parent, row, label, variable, setting):
        self._label(parent, label).grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=4,
        )
        ttk.Entry(
            parent, textvariable=variable, state="readonly",
        ).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(
            parent, text="Browse", style="Settings.TButton",
            command=lambda var=variable, key=setting: self._browse_library(
                var, key,
            ),
        ).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=4)

    def _browse_library(self, variable, setting):
        initialdir = variable.get().strip() or str(Path.home())
        folder = filedialog.askdirectory(
            parent=self.winfo_toplevel(),
            title="Choose library folder",
            initialdir=initialdir,
            mustexist=True,
        )
        if folder:
            variable.set(folder)

    def _show_library_paths(self):
        if self.vars["library_mode"].get() == "library":
            self.library_paths_frame.grid()
        else:
            self.library_paths_frame.grid_remove()

    def _build_about(self, frame):
        self._section_header(
            frame, "About", "Version information and project links.",
        )
        group = self._group(frame, "Moviq")
        with Image.open(resource("assets/moviq-icon.png")) as icon:
            icon = icon.resize((72, 72), Image.Resampling.LANCZOS)
            self.moviq_icon_image = ImageTk.PhotoImage(icon)
        tk.Label(
            group,
            image=self.moviq_icon_image,
            background=self.app.pal["card"],
            borderwidth=0,
        ).grid(row=0, column=0, sticky="w", pady=(5, 0))
        self._label(
            group, f"Moviq  {APP_VERSION}",
            font="TkHeadingFont",
        ).grid(row=1, column=0, sticky="w", pady=5)
        self.update_button = ttk.Button(
            group,
            text="Check for updates",
            style="Settings.TButton",
            command=self._check_for_updates,
        )
        self.update_button.grid(
            row=0, column=1, sticky="e", padx=12, pady=7,
        )
        self.update_status_var = tk.StringVar(value="")
        self.update_status_label = self._label(
            group,
            textvariable=self.update_status_var,
            muted=True,
        )
        self.update_status_label.grid(
            row=1, column=1, sticky="e", padx=12, pady=4,
        )
        self.download_update_button = ttk.Button(
            group,
            text="Download",
            style="Settings.TButton",
            command=lambda: webbrowser.open(self._update_release_url),
        )
        self.download_update_button.grid(
            row=2, column=1, sticky="e", padx=12, pady=4,
        )
        self.download_update_button.grid_remove()
        links = ttk.Frame(group)
        links.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self._link(links, "Release notes", RELEASES_URL).pack(side="left", padx=(0, 16))
        self._link(links, "Report a problem", ISSUES_URL).pack(side="left", padx=(0, 16))
        self._link(links, "Open-source licenses", LICENSE_URL).pack(side="left")
        self._label(
            group,
            TMDB_ATTRIBUTION,
            muted=True,
            wraplength=520,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 4))

    def _check_for_updates(self):
        if self._update_checking:
            return
        self._update_checking = True
        self._update_release_url = None
        self.download_update_button.grid_remove()
        self.update_button.configure(state="disabled")
        self.update_status_var.set("Checking…")
        self.update_status_label.configure(foreground=self.app.pal["muted"])
        self._executor.submit(self._check_for_updates_worker)

    def _check_for_updates_worker(self):
        result = check_latest_release(APP_VERSION)
        self._queue.put(("update", *result))

    def _set_key_status(self, status):
        status_colors = self.app.pal.get("status_colors", {})
        colors = {
            "Connected": self.app.pal.get(
                "done", status_colors.get("auto", (self.app.pal["muted"],))[0],
            ),
            "Checking": self.app.pal.get(
                "warning", status_colors.get("review", (self.app.pal["muted"],))[0],
            ),
            "Rejected": status_colors.get(
                "error", (self.app.pal["muted"],),
            )[0],
            "Couldn't reach TMDB": status_colors.get(
                "error", (self.app.pal["muted"],),
            )[0],
            "Wrong format": status_colors.get(
                "review", (self.app.pal["muted"],),
            )[0],
            "Not checked": self.app.pal["muted"],
        }
        self.key_status_var.set(f"● {status}")
        self.key_status_label.configure(
            foreground=colors.get(
                status,
                colors["Rejected"] if status.startswith("Couldn't save")
                else self.app.pal["muted"],
            ),
        )

    def _link(self, parent, text, url):
        link = self._label(parent, text, cursor="hand2")
        link.configure(foreground=self.app.pal["accent"])
        link.bind("<Button-1>", lambda _event, target=url: webbrowser.open(target))
        return link

    def _bind_preferences(self):
        keys = {
            "key": self.vars["key"],
            "remember": self.vars["remember"],
            "theme": self.vars["theme"],
            "threshold": self.vars["threshold"],
            "auto_match": self.vars["auto_match"],
            "confirm_apply": self.vars["confirm_apply"],
            "tag_id": self.vars["tag_id"],
            "rename_subtitles": self.vars["rename_subtitles"],
            "library_mode": self.vars["library_mode"],
            "movies_library": self.vars["movies_library"],
            "tv_library": self.vars["tv_library"],
        }
        self._preference_traces = []
        for key, variable in keys.items():
            trace_id = variable.trace_add(
                "write",
                lambda *_args, setting=key, var=variable: self._preference_changed(
                    setting, var,
                ),
            )
            self._preference_traces.append((variable, trace_id))
        tv_folder_trace = self.vars["tv_folder"].trace_add(
            "write", lambda *_args: self._legacy_tv_folder_changed(),
        )
        self._preference_traces.append(
            (self.vars["tv_folder"], tv_folder_trace),
        )

    def _legacy_tv_folder_changed(self):
        if self._syncing_tv_folder:
            return
        enabled = self.vars["tv_folder"].get()
        self._updating_format.add("tv")
        try:
            self.vars["tv_create_show_folder"].set(enabled)
            self.vars["tv_create_season_folder"].set(enabled)
        finally:
            self._updating_format.discard("tv")
        self._update_pattern_editing("tv")
        self._formatting_changed("tv")

    def _preference_changed(self, key, variable):
        value = variable.get()
        if key == "key":
            self._key_generation += 1
            self._set_key_status("Not checked")
            self._schedule_key_check(800)
        elif key == "threshold":
            value = min(100, max(50, int(round(float(value) / 5) * 5)))
            if variable.get() != value:
                variable.set(value)
        self.store.set(key, value)

    def _toggle_key_visibility(self):
        show = not bool(self.key_entry.cget("show") == "")
        self.key_entry.configure(show="" if show else "•")
        self.key_visibility_button.configure(text="Hide" if show else "Show")

    def _copy_api_key(self, _event=None):
        try:
            start = self.key_entry.index("sel.first")
            end = self.key_entry.index("sel.last")
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(self.vars["key"].get()[start:end])
        return "break"

    def _schedule_key_check(self, delay):
        if self._key_after is not None:
            self.after_cancel(self._key_after)
        key = self.vars["key"].get().strip()
        generation = self._key_generation
        if not key:
            self._set_key_status("Not checked")
            return
        if not self._V3_KEY.fullmatch(key):
            self._set_key_status("Wrong format")
            return
        self._key_after = self.after(
            delay,
            lambda: self._submit_key_check(key, generation),
        )

    def _submit_key_check(self, key, generation):
        self._key_after = None
        self._set_key_status("Checking")
        self._executor.submit(
            self._check_key_worker, key, generation,
        )

    def _check_key_worker(self, key, generation):
        status = check_tmdb_key(key)
        self._queue.put(("key", generation, key, status))

    def test_key(self):
        key = self.vars["key"].get().strip()
        self._key_generation += 1
        generation = self._key_generation
        if not key:
            self._set_key_status("Not checked")
            return
        if not self._V3_KEY.fullmatch(key):
            self._set_key_status("Wrong format")
            return
        self._submit_key_check(key, generation)

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                message = self._queue.get_nowait()
                if message[0] == "key":
                    _, generation, key, status = message
                    if (
                        generation == self._key_generation
                        and key == self.vars["key"].get().strip()
                    ):
                        self._set_key_status(status)
                elif message[0] == "update":
                    _, result, latest, release_url = message
                    self._update_checking = False
                    self.update_button.configure(state="normal")
                    if result == "current":
                        self.update_status_var.set(
                            f"You're up to date (v{APP_VERSION}).",
                        )
                        self._update_release_url = None
                    elif result == "available":
                        self.update_status_var.set(
                            f"Version {latest} is available.",
                        )
                        self._update_release_url = release_url
                        self.download_update_button.grid()
                    else:
                        self.update_status_var.set(
                            "Couldn't check for updates. Try again later.",
                        )
                        self._update_release_url = None
                    self.update_status_label.configure(
                        foreground=self.app.pal["muted"],
                    )
                elif message[0] == "save_error":
                    self._set_key_status(
                        f"Couldn't save settings: {message[1]}",
                    )
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_id = self.after(100, self._poll)

    def _show_section(self, name):
        self._current_section = name
        for frame in self.section_frames.values():
            frame.grid_remove()
        self.section_frames[name].grid()
        self.content_canvas.yview_moveto(0)
        self.after_idle(self._update_scroll_region)

    def recolor(self, palette):
        self._configure_settings_styles(palette)
        self.content_canvas.configure(background=palette["bg"])
        self._set_format_selector_theme(palette)
        self._recolor_styled_entries(palette)
        for text in self._rich_texts:
            text.configure(
                background=palette["card"],
                foreground=palette["muted"],
            )
            for tag in ("tmdb_link", "api_link"):
                text.tag_configure(tag, foreground=palette["accent"])
            text.tag_configure("bold", foreground=palette["muted"])
        for label, muted in self._labels:
            try:
                label.configure(foreground=(
                    palette["muted"] if muted else palette["fg"]
                ))
            except tk.TclError:
                pass
        for label in (self.key_status_label,):
            try:
                label.configure(foreground=palette["muted"])
            except tk.TclError:
                pass
        for kind in ("movie", "tv"):
            self._recolor_format_panel(kind, palette)
            self._refresh_preset_cards(kind)
        self._set_key_status(
            self.key_status_var.get().removeprefix("● "),
        )

    def close(self):
        """Stop scheduled UI work and background network jobs."""
        self._closed = True
        root = self._root()
        for sequence, binding_id in self._scroll_bindings:
            root._unbind(("bind", "all", sequence), binding_id)
        self._scroll_bindings.clear()
        if self._key_after is not None:
            try:
                self.after_cancel(self._key_after)
            except tk.TclError:
                pass
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        for variable, trace_id in getattr(self, "_preference_traces", ()):
            try:
                variable.trace_remove("write", trace_id)
            except tk.TclError:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
