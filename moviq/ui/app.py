import contextlib
import errno
import copy
import logging
import os
import queue
import re
import stat
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, ttk

from .. import trace
from .. import __version__ as APP_VERSION
from ..core.config import (
    ERROR_LOG_FILE, IMAGE_EXT, SIDECAR_EXT, USER_DATA_DIR, VIDEO_EXT,
    remove_legacy_cache_files,
)
from ..core.file_ops import (
    count_videos, is_hidden_path, make_plan, make_tv_plan, perform_plan,
    rebase, scan_dir, tv_root_for_folder, undo_ops,
)
from ..core.apply import build_plans
from ..core.formatting import (
    MOVIE_DEFAULT_ID, TV_DEFAULT_ID, format_pattern,
)
from ..core.matching import (
    _is_generic_title, group_match_items, normalize_search_title, score,
    tv_match_is_applicable, tv_score,
)
from ..core.list_filter import is_error_item, matches_list_filter
from ..core.match_pipeline import match_movie_items, match_tv_items
from ..core.models import Item
from ..core.network_monitor import NetworkMonitor
from ..core.rename_service import RenameService
from ..core.undo_journal import UndoJournal, read_ops
from ..core import network_state
from ..core.providers.ids import display_id, provider_of
from ..core.naming import (
    NAMING, has_explicit_episode_marker, parse_name, parse_tv_name,
)
from ..core.tmdb import (
    MAX_WORKERS, TMDB, TMDBAuthError, TMDBCancelled, TMDBError,
    TMDBOperationCache,
)
from ..core.providers.router import ProviderRouter
from ..core.providers.settings import (
    PROVIDER_INFO, SOURCE_SELECTIONS, ProviderSettings,
)
from ..core.show_resolver import (
    ShowResolver, ShowResult, ShowState,
)
from .design import LANGUAGES, PALETTES
from .controls import (
    TableTooltip,
)
from .dialogs import SearchDialog
from .popups import (
    ApiKeyPromptDialog, MatchProgressPopup, RenameConfirmationDialog,
    RenameProgressPopup, _show, ask_yes_no, show_error, show_info,
    show_warning,
)
from ..core.settings_store import SettingsStore, resolve_system_theme
from .settings import SettingsView
from .icons import draw_icon
from .table_layout import target_name_display
from .main_page import (
    build_main_page as build_app_main_page,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAVE_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAVE_DND = False

from .theme import (
    HAVE_SV, apply_app_palette, apply_theme as apply_root_theme,
    register_theme_listener, unregister_theme_listener,
)
from .window_utils import wheel_steps


def _storage_gone(error, path):
    cause = error
    while cause is not None:
        if isinstance(cause, OSError) and (
            getattr(cause, "winerror", None)
            in (21, 433, 483, 1117, 121, 1167)
            or cause.errno in (errno.ENODEV, errno.EIO, errno.ENXIO)
        ):
            return True
        cause = (
            getattr(cause, "__cause__", None)
            or getattr(cause, "__context__", None)
        )
    root = Path(path).anchor
    return bool(root) and not Path(root).exists()


class _SensitiveLogFilter(logging.Filter):
    def __init__(self, app):
        super().__init__()
        self.app = app

    @staticmethod
    def _provider_secrets(app):
        values = []
        config = getattr(app, "cfg", {}) or {}
        if isinstance(config, dict):
            key = config.get("key")
            if isinstance(key, str) and key.strip():
                values.append(key.strip())
            provider_keys = config.get("provider_keys")
            if isinstance(provider_keys, dict):
                for value in provider_keys.values():
                    if isinstance(value, str) and value.strip():
                        values.append(value.strip())
        provider_settings = getattr(app, "provider_settings", None)
        if provider_settings is not None:
            for _, value in getattr(provider_settings, "keys", ()):
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
        key_var = getattr(app, "key_var", None)
        if key_var is not None:
            value = key_var.get().strip()
            if value:
                values.append(value)
        return values

    @staticmethod
    def _redact(message, secrets):
        redacted = str(message)
        redacted = re.sub(
            r"(?i)(api_key|apikey)=([^&\s\"'<>]+)",
            r"\1=[redacted]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)(api_key|apikey)\s*[:=]\s*[\"']?([^\"'\s,;]+)",
            r"\1=[redacted]",
            redacted,
        )
        for secret in sorted(
            {value for value in secrets if value},
            key=len,
            reverse=True,
        ):
            if secret:
                redacted = redacted.replace(secret, "[redacted]")
        return redacted

    def filter(self, record):
        try:
            record.msg = self._redact(
                record.getMessage(),
                self._provider_secrets(self.app),
            )
            record.args = ()
        except Exception:
            pass
        return True


MANUAL_REVIEW_STATUSES = {"pending", "review", "nomatch", "error"}
SEARCH_MAX_WIDTH = 320
CONFIG_FIELDS = {
    "key": lambda app: app.key_var.get().strip(),
    "theme": lambda app: app.theme_var.get(),
    "language": lambda app: app.lang_var.get(),
    "threshold": lambda app: app.threshold_pct(),
    "auto_match": lambda app: app.auto_match.get(),
    "confirm_apply": lambda app: app.confirm_apply.get(),
    "name_template": lambda app: app.name_template.get(),
    "movie_preset_id": lambda app: app.movie_preset_id.get(),
    "movie_folder_pattern": lambda app: app.movie_folder_pattern.get(),
    "movie_file_pattern": lambda app: app.name_template.get(),
    "movie_create_folder": lambda app: app.movie_folder.get(),
    "tag_id": lambda app: app.tag_id.get(),
    "media_mode": lambda app: app.media_mode.get(),
    "tv_template": lambda app: app.tv_template.get(),
    "tv_preset_id": lambda app: app.tv_preset_id.get(),
    "tv_show_folder_pattern": lambda app: app.tv_show_folder_pattern.get(),
    "tv_season_folder_pattern": lambda app: app.tv_season_folder_pattern.get(),
    "tv_file_pattern": lambda app: app.tv_template.get(),
    "tv_create_show_folder": lambda app: app.tv_create_show_folder.get(),
    "tv_create_season_folder": lambda app: app.tv_create_season_folder.get(),
    "movie_folder": lambda app: app.movie_folder.get(),
    "tv_folder": lambda app: app.tv_folder.get(),
    "rename_subtitles": lambda app: app.rename_subtitles.get(),
    "library_mode": lambda app: app.library_mode.get(),
    "movies_library": lambda app: app.movies_library.get(),
    "tv_library": lambda app: app.tv_library.get(),
    "window_geometry": lambda app: app._window_geometry(),
    "window_state": lambda app: (
        "zoomed" if app.root.state() == "zoomed" else "normal"
    ),
    "last_folder": lambda app: app._last_folder,
}
ERROR_STATUS_DETAILS = {
    "wrong_mode_movie_in_tv": (
        "This file looks like a movie (no season or episode in the name), "
        "but Moviq is in TV Shows mode. Switch to the Movies tab."
    ),
    "wrong_mode_tv_in_movies": (
        "This file looks like a TV episode ({code}), but Moviq is in Movies "
        "mode. Switch to the TV Shows tab."
    ),
    "file_missing": "The file no longer exists at its original location.",
    "name_conflict": (
        "A file with the new name already exists in the destination."
    ),
    "access_denied": (
        "Windows won't let Moviq change this file. Close any program using it."
    ),
    "bad_path": (
        "The new path is too long or has characters Windows doesn't allow."
    ),
    "tmdb_unavailable": (
        "Couldn't reach the metadata source (no connection, bad API key, "
        "or rate limit). Check Settings and try again."
    ),
}


def _show_in_file_manager(path):
    path = Path(path).absolute()
    if sys.platform == "win32":
        if path.is_dir():
            os.startfile(str(path))
        else:
            explorer = os.path.join(
                os.environ["SystemRoot"], "explorer.exe",
            )
            subprocess.Popen([explorer, "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", "--", str(path)])
    else:
        subprocess.Popen(["xdg-open", "--", str(path.parent)])


def _detect_media_mode(paths, current_mode):
    """Infer a clear mode mismatch from parsed media types."""
    files = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend((file_path, path) for file_path in scan_dir(path))
        elif path.suffix.lower() in VIDEO_EXT:
            files.append((path, path.parent))
    return _detect_mode_from_files(files, current_mode)


def _detect_mode_from_files(files, current_mode):
    episodes = 0
    movies = 0
    for path, root in files:
        item = Item(path, root)
        if item.media_type == "tv":
            episodes += 1
        elif item.movie_like or parse_name(path.stem)[1]:
            movies += 1

    if current_mode == "movie" and episodes > movies:
        return "tv"
    if current_mode == "tv" and movies > episodes:
        return "movie"
    return None


def _resolver_client(key, language, providers, operation_cache):
    return ProviderRouter(
        providers or ProviderSettings(), key, language,
        operation_cache=operation_cache,
    )


class App:
    def __init__(self, root, smoke_test=False):
        self.root = root
        self._smoke_test = smoke_test
        self._root_was_viewable = bool(root.winfo_viewable())
        if self._root_was_viewable:
            root.withdraw()
        root.title(f"Moviq {APP_VERSION}")
        self.UI_SCALE = float(root.winfo_fpixels("1i")) / 96
        self.settings_store = SettingsStore(
            on_change=self._setting_changed,
            on_batch_done=self._flush_pending_refresh,
        )
        self.cfg = self.settings_store.data

        self.items = []
        self.by_iid = {}
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.network_events = queue.Queue()
        self._network_monitor = NetworkMonitor(self.network_events)
        self._network_monitor.start()
        self._rename_events = queue.Queue()
        self._rename_cancel_event = threading.Event()
        self._rename_thread = None
        self._rename_before = None
        self._rename_progress = None
        self.job_total = self.job_done = 0
        self._match_run_id = 0
        self._matching_run = None
        self._match_progress_dialog = None
        self.undo_stack = []
        self.redo_stack = []
        self._undo_journal = None
        self._rename_running = False
        self._manual_match_items = []
        self._manual_match_queue = []
        self._manual_match_queue_items = {}
        self._manual_match_index = 0
        self._manual_match_active = False
        self._manual_match_before = []
        self._manual_match_saved = set()
        self._manual_match_renamed_skipped = 0
        self._manual_operation_cache = None
        self._suppress_undo_record = False
        self._review_filter = False
        self._status_filter = None
        self._filtered_out = set()
        self._search_after = None
        self._search_query = ""
        self._search_new_names = {}
        self._vcount = {}
        self._last_folder = self.cfg.get("last_folder", "")
        self._file_log_handler = RotatingFileHandler(
            ERROR_LOG_FILE,
            maxBytes=1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        self._file_log_handler.setLevel(logging.WARNING)
        self._file_log_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
        ))
        self._file_log_handler.addFilter(_SensitiveLogFilter(self))
        logging.getLogger().addHandler(self._file_log_handler)
        self._workers_closing = False
        self._match_worker_counter = 0
        self.theme_preference = self.cfg.get("theme", "dark")
        self.mode = (
            resolve_system_theme() if self.theme_preference == "system"
            else self.theme_preference
        )
        self.pal = PALETTES[self.mode]
        self._status_tooltip = TableTooltip(
            root, self.pal["tooltip_bg"], self.pal["fg"],
            self.pal["chip_border"],
            self.pal["muted"],
        )
        self._theme_listener = self._apply_theme_listener
        register_theme_listener(root, self._theme_listener)
        self.have_sv = HAVE_SV
        self._sum_pending = False
        self.anchor = None
        self._row_tooltip_paths = {}
        self._status_card_cache_key = None
        self._status_card_cache = None

        remove_legacy_cache_files()

        self.provider_settings = ProviderSettings.from_config(
            self.settings_store.data,
        )
        self.build_ui()
        self.root.update_idletasks()
        self._restore_window_geometry(
            self.cfg.get("window_geometry"),
            self.cfg.get("window_state", "normal"),
        )
        if self._root_was_viewable:
            root.deiconify()
        root.bind_all(
            "<Button-1>", self._clear_focus_after_pointer_click, add="+",
        )
        self.show_resolver = ShowResolver(
            self.key_var.get().strip(), self.language(),
            client_factory=_resolver_client,
            providers=self.provider_settings,
        )
        if self.show_resolver.load_error:
            show_error(
                self, "TV show overrides",
                str(self.show_resolver.load_error),
            )
        self._worker_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._worker_threads = []
        self._ensure_match_workers()
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._system_theme_after = root.after(
            1000, self._poll_system_theme,
        )
        self.root.after_idle(self._prompt_pending_undo_journal)

    def _prompt_pending_undo_journal(self):
        try:
            journal_ops = read_ops()
        except Exception:
            journal_ops = []
        if not journal_ops:
            return
        restore = _show(
            self,
            "Restore interrupted rename?",
            "Moviq was interrupted during a rename.\n"
            "Restore files to their previous locations?",
            "question",
            [("Ignore", False), ("Restore", True)],
            1,
            False,
        )
        if restore:
            undo_ops(journal_ops)
        from ..core.undo_journal import clear_journal
        clear_journal()

    def _clear_focus_after_pointer_click(self, event):
        widget = event.widget
        while widget is not None:
            if getattr(widget, "_moviq_focus_control", False):
                return
            if widget.winfo_class() in {
                "Entry", "Text", "TEntry", "TCombobox", "Spinbox",
                "TSpinbox",
            }:
                return
            widget = getattr(widget, "master", None)
        self.root.after_idle(self.root.focus_set)

    def _restore_window_geometry(self, geometry, window_state="normal"):
        self.root.update_idletasks()
        match = re.fullmatch(
            r"(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?", str(geometry or ""),
        )
        if match:
            width, height = int(match.group(1)), int(match.group(2))
            x = int(match.group(3) or 0)
            y = int(match.group(4) or 0)
        else:
            width, height, x, y = 1180, 720, 0, 0

        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        min_width = min(
            self.header_layout.minimum_window_width(), screen_width,
        )
        min_height = min(round(560 * self.UI_SCALE), screen_height)
        self._window_min_height = min_height
        self._set_window_minsize(min_width, min_height)
        if not match:
            width = min(
                self.header_layout.default_window_width(),
                round(screen_width * 0.9),
            )
            height = round(720 * self.UI_SCALE)
        width = min(screen_width, max(min_width, width))
        height = min(screen_height, max(min_height, height))
        x = min(max(0, x), max(0, screen_width - width))
        y = min(max(0, y), max(0, screen_height - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.update_idletasks()
        if sys.platform == "win32" and window_state == "zoomed":
            self.root.state("zoomed")

    def _set_window_minsize(self, width, height):
        size = (int(width), int(height))
        if getattr(self, "_last_minsize", None) == size:
            return
        self.root.minsize(*size)
        self._last_minsize = size

    def _window_geometry(self):
        self.root.update_idletasks()
        return self.root.geometry()

    def register_drop_targets(self, widget):
        if not HAVE_DND:
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", self.on_drop)
        for child in widget.winfo_children():
            self.register_drop_targets(child)

    # ---- config ----------------------------------------------------------
    def save_config(self):
        values = {
            key: read_value(self)
            for key, read_value in CONFIG_FIELDS.items()
        }
        self.settings_store.update(values)
        self.settings_store.save_now()

    def on_close(self):
        if getattr(self, "_rename_running", False):
            rename_thread = getattr(self, "_rename_thread", None)
            if rename_thread is None or not rename_thread.is_alive():
                self._rename_running = False
            elif not ask_yes_no(
                self,
                "Rename in progress",
                "Moviq is still renaming files. Quit anyway?",
            ):
                return
            else:
                self._rename_cancel_event.set()
        self._network_monitor.stop()
        self._hide_tree_tooltip()
        self._workers_closing = True
        if self._search_after is not None:
            self.root.after_cancel(self._search_after)
            self._search_after = None
        if self._system_theme_after is not None:
            self.root.after_cancel(self._system_theme_after)
            self._system_theme_after = None
        if self._matching_run:
            self._matching_run["cancel_event"].set()
            self._discard_match_cache(self._matching_run)
        for _ in range(MAX_WORKERS):
            self.jobs.put(None)
        self._worker_pool.shutdown(wait=False, cancel_futures=True)
        logging.getLogger().removeHandler(self._file_log_handler)
        self._file_log_handler.close()
        try:
            self.save_config()
        except OSError as error:
            logging.getLogger(__name__).exception(
                "Could not save settings during shutdown.",
            )
            show_warning(self, "Settings", f"Could not save settings:\n{error}")
        if hasattr(self, "settings_view"):
            self.settings_view.close()
        unregister_theme_listener(self.root, self._theme_listener)
        self.root.destroy()

    # ---- UI ---------------------------------------------------------------
    def setup_fonts(self):
        fams = set(tkfont.families())
        fam = next((f for f in ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text",
                                "Helvetica Neue", "Ubuntu", "Noto Sans",
                                "DejaVu Sans") if f in fams), None)
        fam = fam or tkfont.nametofont("TkDefaultFont").actual("family")
        tkfont.nametofont("TkDefaultFont").configure(
            family=fam, size=10, weight="normal",
        )
        tkfont.nametofont("TkTextFont").configure(
            family=fam, size=10, weight="normal",
        )
        tkfont.nametofont("TkHeadingFont").configure(
            family=fam, size=10, weight="bold",
        )
        tkfont.nametofont("TkMenuFont").configure(
            family=fam, size=10, weight="normal",
        )
        self.title_font = tkfont.Font(
            self.root, name="MoviqTitleFont", family=fam or "TkDefaultFont",
            size=18, weight="bold",
        )
        if hasattr(self, "header_layout"):
            self.apply_theme(self.mode)

    def apply_theme(self, mode):
        return apply_root_theme(self.root, mode)

    def _apply_theme_listener(self, mode):
        apply_app_palette(self, mode)
        self.update_theme_button()

    def update_theme_button(self):
        preference = self.theme_preference
        names = {"light": "Light", "dark": "Dark", "system": "System"}
        next_preference = {
            "light": "dark", "dark": "system", "system": "light",
        }[preference]
        next_name = names[next_preference]
        if hasattr(self, "toolbar_icon_group"):
            self.toolbar_icon_group.set_icon("theme", preference)
            tooltip = f"Theme: {names[preference]}. Click for {next_name}"
            self.toolbar_icon_group.set_tooltip("theme", tooltip)
            if getattr(self.toolbar_icon_group, "_hover_index", None) == 2:
                self.icon_group_tooltip.set_text(tooltip)
        if hasattr(self, "header_layout"):
            self.header_layout.update_overflow_menu()

    def cycle_theme(self):
        next_preference = {
            "light": "dark", "dark": "system", "system": "light",
        }[self.theme_preference]
        self.theme_var.set(next_preference)

    def _poll_system_theme(self):
        self._system_theme_after = None
        if self.theme_preference == "system":
            mode = resolve_system_theme(self.mode)
            if mode != self.mode:
                self.apply_theme(mode)
        self._system_theme_after = self.root.after(
            1000, self._poll_system_theme,
        )

    def _apply_header_theme(self):
        pal = self.pal
        buttons = [
            self.rename_all_button, self.add_files_button,
            self.add_folder_button, self.auto_match_button,
            self.manual_match_button, self.overflow_button,
        ]
        for button in buttons:
            button.set_theme(
                background=pal["header_btn"],
                border=pal["header_border"],
                hover_background=pal["header_btn_hi"],
                hover_border=pal["header_border_hi"],
                foreground=pal["fg"], muted=pal["header_muted"],
                accent=pal["header_accent"],
                accent_hover=pal["accent_hi"],
                accent_foreground=pal["accent_text"],
                canvas_background=pal["header_bg"],
            )
        self.rename_all_button.set_appearance(
            pal["switch_active"],
            pal["header_accent"],
            pal["header_accent"],
            active=True,
        )
        self.toolbar_icon_group.set_theme(
            background=pal["header_btn"],
            border=pal["header_border"],
            hover_background=pal["header_btn_hi"],
            foreground=pal["fg"], muted=pal["header_muted"],
            canvas_background=pal["header_bg"],
        )
        self.header_icon_separator.configure(
            background=pal["header_separator"],
        )
        self.header_mode_separator.configure(
            background=pal["header_separator"],
        )
        self.header_match_separator.configure(
            background=pal["header_separator"],
        )
        self.search_box.set_palette(pal)
        self._refresh_filter_chip_styles()
        self.logo_canvas.configure(background=pal["header_bg"])
        self.logo_canvas.delete("all")
        logo_width = round(40 * self.UI_SCALE)
        logo_height = round(40 * self.UI_SCALE)
        self.logo_canvas.create_image(
            logo_width / 2, logo_height / 2,
            image=self.logo_image,
        )
        self._position_status_cells()
        self.empty_icon.configure(bg=pal["card"])
        self.empty_icon.delete("all")
        draw_icon(
            self.empty_icon, "folder-plus", 36, pal["accent"], (32, 27),
        )
        self.menu.configure(bg=pal["card"], fg=pal["fg"],
                            activebackground=pal["accent"],
                            activeforeground="#ffffff")
        self.overflow_menu.configure(
            bg=pal["card"], fg=pal["fg"],
            activebackground=pal["accent"],
            activeforeground="#ffffff",
            disabledforeground=pal["muted"],
        )
        self.overflow_tooltip.background = pal["card"]
        self.overflow_tooltip.foreground = pal["fg"]
        if hasattr(self, "settings_view"):
            self.settings_view.recolor(pal)
        if hasattr(self, "header_layout"):
            self.header_layout.remeasure()

    # ---- settings values ----------------------------------------------------
    def language(self):
        return (self.lang_var.get().split() or ["en-US"])[0]

    def threshold_pct(self):
        try:
            return min(100, max(50, int(round(self.thr_var.get() / 5) * 5)))
        except (tk.TclError, ValueError):
            return 90

    def threshold(self):
        return self.threshold_pct() / 100

    def sync_settings(self):
        NAMING["template"] = (
            self.name_template.get().strip()
            or "{title} ({year})"
        )
        NAMING["tv_template"] = (
            self.tv_template.get().strip()
            or "{title} - S{season:00}E{episode:00} - {episode_title}"
        )
        NAMING["tag_id"] = False
        self._refresh_all_or_defer()

    def _refresh_all_or_defer(self):
        """Refresh every row now, or once after a multi-key settings update."""
        store = getattr(self, "settings_store", None)
        if getattr(store, "batching", False):
            self._refresh_pending = True
        else:
            self.refresh_all()

    def _flush_pending_refresh(self):
        if getattr(self, "_refresh_pending", False):
            self._refresh_pending = False
            self.refresh_all()

    def _setting_changed(self, key, value):
        if key in (
            "name_template", "tv_template", "movie_file_pattern",
            "tv_file_pattern", "movie_folder_pattern",
            "tv_show_folder_pattern", "tv_season_folder_pattern",
        ):
            self.sync_settings()
        elif key == "movie_create_folder":
            self._refresh_all_or_defer()
        elif key in ("tv_create_show_folder", "tv_create_season_folder"):
            self._refresh_all_or_defer()
        elif key in (
            "library_mode", "movies_library", "tv_library",
        ):
            self._refresh_all_or_defer()
        elif key == "theme":
            self.theme_preference = value
            if hasattr(self, "settings_view"):
                theme = (
                    resolve_system_theme(self.mode)
                    if value == "system" else value
                )
                self.apply_theme(theme)
        elif key == "media_mode":
            self.media_mode.set(value)

    # ---- pages --------------------------------------------------------------
    def build_ui(self):
        self.setup_fonts()
        self.sw = (
            "Moviq.Switch.TCheckbutton" if HAVE_SV else "TCheckbutton"
        )
        c = self.cfg
        self.key_var = tk.StringVar(value=c.get("key", ""))
        self.show_key = tk.BooleanVar(value=False)
        self.lang_var = tk.StringVar(value=c.get("language", LANGUAGES[0]))
        self.thr_var = tk.DoubleVar(value=min(
            100, max(50, int(round(float(c.get("threshold", 90)) / 5) * 5)),
        ))
        self.auto_match = tk.BooleanVar(value=c.get("auto_match", True))
        self.confirm_apply = tk.BooleanVar(value=c.get("confirm_apply", True))
        self.name_template = tk.StringVar(value=c.get(
            "movie_file_pattern", c.get("name_template", "{title} ({year})"),
        ))
        self.movie_folder_pattern = tk.StringVar(value=c.get(
            "movie_folder_pattern", "{title} ({year})",
        ))
        self.movie_preset_id = tk.StringVar(value=c.get(
            "movie_preset_id", MOVIE_DEFAULT_ID,
        ))
        self.tag_id = tk.BooleanVar(value=c.get("tag_id", False))
        self.theme_var = tk.StringVar(value=self.theme_preference)
        self.theme_var.trace_add(
            "write",
            lambda *_args: self.settings_store.set(
                "theme", self.theme_var.get(),
            ),
        )
        self.tv_template = tk.StringVar(value=c.get(
            "tv_file_pattern",
            c.get(
                "tv_template",
                "{title} - S{season:00}E{episode:00} - {episode_title}",
            ),
        ))
        self.tv_preset_id = tk.StringVar(value=c.get(
            "tv_preset_id", TV_DEFAULT_ID,
        ))
        self.tv_show_folder_pattern = tk.StringVar(value=c.get(
            "tv_show_folder_pattern", "{title}",
        ))
        self.tv_season_folder_pattern = tk.StringVar(value=c.get(
            "tv_season_folder_pattern", "Season {season:00}",
        ))
        self.movie_folder = tk.BooleanVar(value=c.get(
            "movie_create_folder", c.get("movie_folder", True),
        ))
        self.tv_create_show_folder = tk.BooleanVar(value=c.get(
            "tv_create_show_folder", c.get("tv_folder", True),
        ))
        self.tv_create_season_folder = tk.BooleanVar(value=c.get(
            "tv_create_season_folder", c.get("tv_folder", True),
        ))
        self.tv_folder = tk.BooleanVar(value=(
            self.tv_create_show_folder.get()
            and self.tv_create_season_folder.get()
        ))
        self.rename_subtitles = tk.BooleanVar(
            value=c.get("rename_subtitles", True),
        )
        self.library_mode = tk.StringVar(
            value=c.get("library_mode", "in_place"),
        )
        self.movies_library = tk.StringVar(value=c.get("movies_library", ""))
        self.tv_library = tk.StringVar(value=c.get("tv_library", ""))
        self.media_mode = tk.StringVar(
            value=c.get("media_mode", "movie")
            if c.get("media_mode", "movie") in ("movie", "tv") else "movie"
        )

        self.container = ttk.Frame(self.root, padding=(20, 18, 20, 12))
        self.container.pack(fill="both", expand=True)
        self.page_main = ttk.Frame(self.container)
        self.page_settings = ttk.Frame(self.container)
        self.build_main_page(self.page_main)
        self.build_settings_page(self.page_settings)

        self.register_drop_targets(self.root)

        self.apply_theme(self.mode)
        self.sync_settings()
        self.update_summary()
        self.show_page("main")

    def show_page(self, name):
        self.page_main.pack_forget()
        self.page_settings.pack_forget()
        self.container.configure(
            padding=(0, 0, 0, 0) if name == "settings"
            else (20, 18, 20, 12),
        )
        (self.page_main if name == "main" else self.page_settings).pack(
            fill="both", expand=True)
        if name == "main":
            self._schedule_status_cells()
        else:
            self.settings_view.on_network_change(
                self._network_monitor.is_online,
            )

    def leave_settings(self):
        try:
            self.save_config()
        except OSError as error:
            show_warning(self, "Settings", f"Could not save settings:\n{error}")
        self.show_page("main")

    def build_main_page(self, page):
        return build_app_main_page(self, page)

    def fit_table_columns(self, _event=None):
        if not hasattr(self, "tree"):
            return
        available = max(
            0, self.tree.winfo_width()
            - self.status_column_width - self.source_column_width,
        )
        original_width = max(
            self.table_name_min_width, available // 2,
        )
        new_width = max(
            self.table_name_min_width, available - original_width,
        )
        changed = False
        for column, width in (
            ("#0", original_width), ("new", new_width),
        ):
            if int(self.tree.column(column, "width")) != width:
                self.tree.column(column, width=width)
                changed = True
        if changed and hasattr(self, "items"):
            self._schedule_status_cells()

    def _ignore_column_separator(self, event):
        if self.tree.identify_region(event.x, event.y) == "separator":
            self.tree.configure(cursor="arrow")
            return "break"
        return None

    @staticmethod
    def _table_status_key(status, error=False):
        if error or status == "error":
            return "error"
        if status == "matching":
            return "matching"
        if status == "manual":
            return "manual"
        if status == "auto":
            return "matched"
        if status == "no_change":
            return "no_change"
        if status in ("nomatch", "done"):
            return status
        if status == "review":
            return "review"
        return "pending"

    @staticmethod
    def _table_status_label(status, error=False):
        if not error and status == "manual":
            return "Confirmed"
        key = App._table_status_key(status, error)
        return {
            "pending": "Pending",
            "matching": "Matching",
            "matched": "Matched",
            "review": "Review",
            "nomatch": "No match",
            "no_change": "No change",
            "done": "Done",
            "error": "Error",
        }[key]

    @staticmethod
    def _table_status_chip(item):
        if item.error or item.status == "error":
            return "Error", "table_status_nomatch"
        if item.status in ("auto", "manual"):
            return "Ready", "table_status_ready"
        if item.status in ("done", "no_change"):
            return "Done", "table_status_done"
        if item.status == "review":
            return "Review", "table_status_review"
        if item.status == "nomatch":
            return "No match", "table_status_nomatch"
        if item.status == "matching":
            return "Matching", "table_status_ready"
        return "Pending", "muted"

    @staticmethod
    def _table_source_label(item):
        match = item.match or getattr(item, "review_match", None) or {}
        match_id = match.get("id")
        if match_id is not None:
            try:
                provider = PROVIDER_INFO[provider_of(match_id)]
            except (KeyError, TypeError, ValueError):
                pass
            else:
                return provider["label"]
        if getattr(item, "match_source", None) == "manual":
            return "Manual"
        return "\u2014"

    def _safe_exception_message(self, message):
        message = str(message)
        key = self.key_var.get().strip()
        if key:
            message = message.replace(key, "[redacted]")
        return re.sub(r"https?://\S+", "[URL]", message, flags=re.IGNORECASE)

    def _error_code_for_exception(self, error):
        if isinstance(error, FileExistsError):
            return "name_conflict"
        if isinstance(error, FileNotFoundError):
            return "file_missing"
        if (
            isinstance(error, PermissionError)
            or getattr(error, "winerror", None) in (5, 32, 33)
        ):
            return "access_denied"
        if (
            getattr(error, "winerror", None) in (123, 206)
            or getattr(error, "errno", None) in (
                errno.ENAMETOOLONG, errno.EINVAL,
            )
        ):
            return "bad_path"
        return "unknown"

    @staticmethod
    def _episode_missing_detail(item):
        count = getattr(item, "season_episode_count", 0)
        if count == 0:
            return (
                f"Season {item.season_number} does not exist on TMDB, "
                f"so episode {item.episode_number} doesn't exist there."
            )
        noun = "episode" if count == 1 else "episodes"
        return (
            f"Season {item.season_number} has only {count} {noun} on TMDB, "
            f"so episode {item.episode_number} doesn't exist there."
        )

    def _status_detail(self, item, new_path=""):
        status_key = self._table_status_key(item.status, bool(item.error))
        if status_key == "pending":
            return "Not matched yet. Run Auto Match."
        if status_key in ("matched", "manual"):
            match = item.match or {}
            title = " ".join(
                str(match.get("title") or item.query or "Unknown").split()
            )
            year = match.get("year") or "????"
            if getattr(item, "match_source", None) == "manual":
                return (
                    f"Confirmed manually: {title} ({year}). "
                    "Change it with Manual Match."
                )
            confidence = (
                100 if item.status == "manual"
                else round(self._match_confidence(item, match))
            )
            if item.media_type == "tv":
                season = match.get("season_number", item.season_number) or 0
                episode = match.get("episode_number", item.episode_number) or 0
                episode_end = match.get("episode_end_number")
                episode_code = f"S{season:02d}E{episode:02d}"
                if isinstance(episode_end, int) and episode_end > episode:
                    episode_code += f"-E{episode_end:02d}"
                show_text = (
                    f"Matched to {title} ({year}) \u00b7 "
                    f"{episode_code}"
                )
            else:
                show_text = f"Matched to {title} ({year})"
            target_name = Path(new_path).name if new_path else item.path.name
            target_name = " ".join(str(target_name).split())
            return (
                f"{show_text} \u00b7 {confidence}% confidence.\n"
                f"Will be renamed to {target_name}."
            )
        if status_key == "review":
            match = (
                item.match or getattr(item, "review_match", None) or {}
            )
            confidence = round(self._match_confidence(item, match))
            reason = getattr(item, "review_reason", "partial")
            if reason == "absolute":
                return (
                    f"Absolute episode number {item.episode_number} "
                    "couldn't be mapped. Check it with Manual Match."
                )
            if reason == "episode_title":
                return (
                    f"Episode title \"{item.episode_title}\" isn't unique "
                    f"for {item.query}. Add the season and episode number "
                    "to the filename."
                )
            if reason == "episode_missing":
                return (
                    f"{self._episode_missing_detail(item)} "
                    "Check it with Manual Match."
                )
            if reason == "year_title":
                explanation = (
                    "The file name is only a number, so the title is a guess"
                )
            elif reason == "year":
                explanation = "The year differs from the file name"
            elif reason == "similar":
                explanation = "Several results are similar"
            elif reason == "offline":
                explanation = (
                    "Guessed from the file name only; nothing was "
                    "looked up online"
                )
            else:
                explanation = "The title is only a partial match"
            title = " ".join(
                str(match.get("title") or item.query or "Unknown").split()
            )
            year = match.get("year") or "????"
            needs_check = reason in (
                "year_title", "year", "similar", "offline",
            )
            if not needs_check and confidence >= 90:
                needs_check = True
                explanation = (
                    "The title matches, but Moviq isn't sure enough "
                    "to rename it automatically"
                )
            if needs_check:
                headline = f"Needs a check: {title} ({year}). "
            else:
                headline = f"Low confidence ({confidence}%): {title} ({year}). "
            return (
                f"{headline}{explanation}.\nCheck it with Manual Match."
            )
        if status_key == "nomatch":
            title = " ".join(str(item.query or "unknown title").split())
            return (
                f'No TMDB result for "{title}".\n'
                "Try Manual Match to search by hand."
            )
        if status_key == "no_change":
            return "Already named correctly. Nothing to rename."
        if status_key == "done":
            return "Renamed this session. Undo the rename to change its match."
        if status_key == "error":
            reason = getattr(item, "error_code", None) or "unknown"
            if reason == "source_mode":
                return item.error or "The selected source does not cover this mode."
            message = ERROR_STATUS_DETAILS.get(reason)
            if message:
                return message.format(
                    code=getattr(item, "episode_code", "S??E??"),
                )
            exception_message = self._safe_exception_message(
                item.error or "Unknown error",
            )
            return f"Something went wrong: {exception_message}."
        return "Matching is in progress."

    @staticmethod
    def _status_match_title(item, match):
        title = " ".join(
            str(match.get("title") or item.query or "").split()
        )
        if not title:
            return ""
        year = match.get("year") or "????"
        result = f"{title} ({year})"
        season = match.get("season_number")
        episode = match.get("episode_number")
        if item.media_type == "tv" and isinstance(season, int) and isinstance(
            episode, int,
        ):
            code = f"S{season:02d}E{episode:02d}"
            episode_end = match.get("episode_end_number")
            if isinstance(episode_end, int) and episode_end > episode:
                code += f"-E{episode_end:02d}"
            episode_title = match.get("episode_title")
            details = code
            if episode_title:
                details += f" \u00b7 {episode_title}"
            result += f" [{details}]"
        return result

    @staticmethod
    def _status_source(match):
        composite_id = match.get("id")
        if composite_id is None:
            return ""
        try:
            return display_id(composite_id)
        except (KeyError, TypeError, ValueError):
            return ""

    @staticmethod
    def _status_title_score(item, match):
        if not match:
            return 0.0
        return (
            tv_score(match, item.query)
            if item.media_type == "tv"
            else score(match, item.query)
        )

    @staticmethod
    def _attached_asset_count(path):
        path = Path(path)
        if not path.parent.is_dir():
            return 0
        extensions = SIDECAR_EXT | IMAGE_EXT
        prefix = path.stem.lower()
        try:
            return sum(
                candidate.is_file()
                and candidate != path
                and candidate.suffix.lower() in extensions
                and candidate.name.lower().startswith(prefix)
                and candidate.name[len(path.stem):].startswith(
                    (".", "-", "_", " "),
                )
                for candidate in path.parent.iterdir()
            )
        except OSError:
            logging.getLogger(__name__).warning(
                "Could not inspect attached files for status card: %s",
                path,
                exc_info=True,
            )
            return 0

    def _status_card(self, item):
        status_key = self._table_status_key(item.status, bool(item.error))
        match = item.match or getattr(item, "review_match", None) or {}
        lines = []

        def add(label, value):
            if value is not None and str(value).strip():
                lines.append((label, str(value)))

        def source_text(candidate):
            return self._status_source(candidate)

        def add_warning():
            error_code = getattr(item, "error_code", None)
            if error_code == "name_conflict":
                add("Warning", "Destination already exists.")
                return
            if error_code == "bad_path":
                add("Warning", "The target path is too long or invalid.")
                return
            target = self._row_tooltip_paths.get(
                item.iid, {},
            ).get("#2", "")
            if target and Path(target).exists() and (
                self._normalized_path(target)
                != self._normalized_path(item.path)
            ):
                add("Warning", "Destination already exists.")
                return
            if status_key == "done":
                original = Path(item.orig_path)
                current_original = Path(item.path).parent / original.name
                if not current_original.parent.is_dir():
                    return
                remaining = self._attached_asset_count(current_original)
                if remaining:
                    noun = "file" if remaining == 1 else "files"
                    add(
                        "Warning",
                        f"{remaining} subtitle/artwork {noun} left behind.",
                    )

        if status_key in ("matched", "no_change"):
            title = self._status_match_title(item, match)
            add("Match", title)
            add("Source", source_text(match))
            add("How", getattr(item, "match_method", None))
            if match:
                add("Title match", f"{item.title_score:.0%}")
            if item.year and match.get("year"):
                add(
                    "Year",
                    "agrees" if item.year == match.get("year") else "differs",
                )
            target = self._row_tooltip_paths.get(
                item.iid, {},
            ).get("#2", "")
            if not target:
                target = str(item.path)
            add("New Name", Path(target).name)
            attached_count = self._attached_asset_count(item.orig_path)
            if attached_count:
                add(
                    "Moves with it",
                    f"{attached_count} subtitle/artwork files",
                )
        elif status_key == "manual":
            title = self._status_match_title(item, match)
            if title and source_text(match):
                title = f"{title} \u00b7 {source_text(match)}"
            add("Match", title)
            add("Picked", "You chose this in Manual Match")
        elif status_key == "review":
            reason = getattr(item, "review_reason", None)
            why = {
                "absolute": (
                    f"Absolute episode number {getattr(item, 'episode_number', 'unknown')} "
                    "couldn't be mapped."
                ),
                "episode_title": (
                    f'Episode title "{getattr(item, "episode_title", "unknown")}" isn\'t unique '
                    f"for {getattr(item, 'query', 'this show')}."
                ),
                "episode_missing": self._episode_missing_detail(item),
                "year_title": (
                    "The file name is only a number, so the title is a guess."
                ),
                "year": "The year differs from the file name.",
                "similar": "Several results are similar.",
                "offline": (
                    "Guessed from the file name only; nothing was looked "
                    "up online."
                ),
            }.get(reason, "The title is only a partial match.")
            if reason == "absolute":
                do_this = "Check the episode number with Manual Match."
            elif reason == "episode_title":
                do_this = (
                    "Add the season and episode number to the filename."
                )
            elif reason == "episode_missing":
                do_this = "Check the episode number with Manual Match."
            elif reason not in (
                "year_title", "year", "similar", "offline",
            ) and item.title_score >= 0.9:
                why = (
                    "The title matches, but Moviq isn't sure enough "
                    "to rename it automatically."
                )
                do_this = "Press Manual Match."
            else:
                do_this = "Press Manual Match."
            add("Why", why)
            add("Do this", do_this)
            suggested_title = self._status_match_title(item, match)
            source = source_text(match)
            if suggested_title and source:
                suggested_title += f" \u00b7 {source}"
            add("Match", suggested_title)
            if match:
                add("Title match", f"{item.title_score:.0%}")
        elif status_key == "nomatch":
            add("Searched", item.query)
            tried = getattr(item, "tried_sources", None) or ()
            add("Tried", ", ".join(str(name) for name in tried))
            if _is_generic_title(item.query):
                why = "Placeholder title."
                do_this = "Press Manual Match."
            else:
                settings = self.provider_settings
                names = (
                    settings.order
                    if settings.selected == "automatic"
                    else (settings.selected,)
                )
                if settings.selected == "automatic":
                    names = tuple(
                        name for name in names if name in settings.enabled
                    )
                usable_source = False
                missing_key = False
                for name in names:
                    info = PROVIDER_INFO.get(name)
                    if not info:
                        continue
                    has_key = (
                        bool(self.key_var.get().strip())
                        if name == "tmdb"
                        else bool(settings.key(name))
                    )
                    if info["needs_key"] and not has_key:
                        missing_key = True
                    elif not info["needs_key"] or has_key:
                        usable_source = True
                if not usable_source and missing_key:
                    why = "Needs a key."
                    do_this = "Add a source in Settings > Sources."
                else:
                    why = "Nothing found."
                    do_this = "Press Manual Match."
            add("Why", why)
            add("Do this", do_this)
        elif status_key == "error":
            reason = getattr(item, "error_code", None) or "unknown"
            message = ERROR_STATUS_DETAILS.get(reason)
            if message:
                message = message.format(
                    code=getattr(item, "episode_code", "S??E??"),
                )
            else:
                message = self._safe_exception_message(
                    item.error or "Unknown error",
                )
            add("Problem", message)
            fix = {
                "wrong_mode_movie_in_tv": "Switch to the Movies tab.",
                "wrong_mode_tv_in_movies": "Switch to the TV Shows tab.",
                "file_missing": "Restore the file or remove it from the list.",
                "name_conflict": "Resolve the destination name conflict.",
                "access_denied": "Close programs using the file and retry.",
                "bad_path": "Shorten the target path or file name.",
                "tmdb_unavailable": "Check the source key and connection.",
            }.get(reason, "Review the error and try again.")
            add("Do this", fix)
        elif status_key == "done":
            original = Path(item.orig_path).name
            current = Path(item.path).name
            add("Renamed", f"{original} -> {current}")
            add("Undo", "Available")

        add_warning()
        return lines

    @staticmethod
    def _match_confidence(item, match):
        if item.media_type == "tv":
            show_result = getattr(item, "show_result", None)
            if show_result is not None:
                return show_result.confidence * 100
            return tv_score(match, item.query) * 100
        return score(match, item.query) * 100

    @staticmethod
    def _middle_clip(text, font, available):
        if font.measure(text) <= available:
            return text
        ellipsis = "\u2026"
        low, high = 0, len(text)
        while low < high:
            keep = (low + high + 1) // 2
            left = (keep + 1) // 2
            right = keep // 2
            candidate = text[:left] + ellipsis + (text[-right:] if right else "")
            if font.measure(candidate) <= available:
                low = keep
            else:
                high = keep - 1
        keep = low
        left = (keep + 1) // 2
        right = keep // 2
        return text[:left] + ellipsis + (text[-right:] if right else "")

    def _destination_display(self, item, destination):
        destination = Path(destination)
        if item.media_type != "tv":
            return (
                target_name_display(destination)
                if self.movie_folder.get() else destination.name
            )
        parts = []
        make_show_folder = self.tv_create_show_folder.get()
        make_season_folder = self.tv_create_season_folder.get()
        if make_show_folder:
            parts.append(
                destination.parent.parent.name
                if make_season_folder else destination.parent.name
            )
        if make_season_folder:
            parts.append(destination.parent.name)
        parts.append(destination.name)
        return os.path.join(*parts)

    def _schedule_tree_tooltip(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        item = self.by_iid.get(row)
        if not item or column not in ("#0", "#1", "#2"):
            self._hide_tree_tooltip()
            return
        cache_key = (
            row, column, item.status_detail, item.status, item.error_code,
        )
        if column == "#1" and cache_key != self._status_card_cache_key:
            card = self._status_card(item)
            self._status_card_cache = card or item.status_detail
            self._status_card_cache_key = cache_key
        if column == "#0":
            content = Path(item.orig_path).name
        elif column == "#2":
            content = self._row_tooltip_paths.get(
                item.iid, {},
            ).get("display", "")
        else:
            content = self._status_card_cache
        self._status_tooltip.schedule(
            (row, column, repr(content)), content,
            event.x_root, event.y_root,
            delay=400,
        )

    def _hide_tree_tooltip(self, _event=None):
        self._status_tooltip.hide()
        self._status_card_cache_key = None
        self._status_card_cache = None

    def on_mousewheel(self, event):
        units = wheel_steps(event)
        if units:
            self.scroll_rows(units)
        return "break"

    def scroll_rows(self, units):
        self._hide_tree_tooltip()
        self.tree.yview_scroll(units, "units")
        self._schedule_status_cells()

    def _hide_tooltip_on_scroll_key(self, event):
        if event.keysym in {
            "Up", "Down", "Prior", "Next", "Home", "End",
        }:
            self._hide_tree_tooltip()

    def _schedule_status_cells(self, _event=None):
        if getattr(self, "_status_cells_pending", False):
            return
        self._status_cells_pending = True
        self.root.after_idle(self._position_status_cells)

    def _position_status_cells(self):
        self._status_cells_pending = False
        if self.tree.winfo_exists():
            self.tree.redraw()

    def build_settings_page(self, page):
        variables = {
            "key": self.key_var,
            "auto_match": self.auto_match,
            "threshold": self.thr_var,
            "confirm_apply": self.confirm_apply,
            "name_template": self.name_template,
            "movie_preset_id": self.movie_preset_id,
            "movie_folder_pattern": self.movie_folder_pattern,
            "movie_file_pattern": self.name_template,
            "movie_create_folder": self.movie_folder,
            "tag_id": self.tag_id,
            "tv_template": self.tv_template,
            "tv_preset_id": self.tv_preset_id,
            "tv_show_folder_pattern": self.tv_show_folder_pattern,
            "tv_season_folder_pattern": self.tv_season_folder_pattern,
            "tv_file_pattern": self.tv_template,
            "tv_create_show_folder": self.tv_create_show_folder,
            "tv_create_season_folder": self.tv_create_season_folder,
            "movie_folder": self.movie_folder,
            "tv_folder": self.tv_folder,
            "rename_subtitles": self.rename_subtitles,
            "library_mode": self.library_mode,
            "movies_library": self.movies_library,
            "tv_library": self.tv_library,
        }
        self.settings_view = SettingsView(
            page, self, self.settings_store, variables,
        )
        self.settings_view.pack(fill="both", expand=True)

    def schedule_summary(self):
        if not self._sum_pending:
            self._sum_pending = True
            self.root.after_idle(self.update_summary)

    def update_summary(self):
        self._sum_pending = False
        self.update_action_labels()
        self.update_detail()
        n = len(self.items)
        counts = {
            "all": n,
            "ready": sum(
                item.status in ("auto", "manual")
                and not is_error_item(item)
                for item in self.items
            ),
            "review": sum(
                item.status == "review" and not is_error_item(item)
                for item in self.items
            ),
            "nomatch": sum(
                item.status == "nomatch" and not is_error_item(item)
                for item in self.items
            ),
            "error": sum(is_error_item(item) for item in self.items),
            "done": sum(
                item.status in ("done", "no_change")
                and not is_error_item(item)
                for item in self.items
            ),
        }
        self._filter_counts = counts
        if hasattr(self, "summary_var"):
            summary_parts = []
            if n:
                summary_parts.append(f"{n} files")
            for count, label in (
                (counts["ready"], "ready"),
                (counts["review"], "review"),
                (counts["nomatch"], "no match"),
                (counts["error"], "errors"),
                (
                    sum(
                        item.status == "done" and not is_error_item(item)
                        for item in self.items
                    ),
                    "renamed",
                ),
            ):
                if count:
                    summary_parts.append(f"{count} {label}")
            self.summary_var.set(" \u00b7 ".join(summary_parts))
            if summary_parts:
                if not self.summary_label.winfo_manager():
                    self.summary_label.pack(
                        side="right", fill="y", padx=(8, 0),
                    )
            else:
                self.summary_label.pack_forget()
        labels = {
            "all": "All", "ready": "Ready", "review": "Review",
            "error": "Error", "done": "Done",
        }
        for key, count in counts.items():
            if hasattr(self, "filter_vars") and key in self.filter_vars:
                self.filter_vars[key].set(f"{labels[key]} {count}")
        if hasattr(self, "filter_control"):
            self._refresh_filter_chip_styles()
        self._schedule_status_cells()
        total_in_scope = sum(
            self._matches_current_filter(item, query="")
            for item in self.items
        )
        visible_count = sum(
            self._matches_current_filter(item)
            for item in self.items
        )
        if hasattr(self, "search_box"):
            self.search_box.set_count(visible_count, total_in_scope)
        if visible_count:
            self.empty_state.place_forget()
        else:
            status_filter = getattr(self, "_status_filter", None)
            if self._search_query:
                self.empty_title_var.set("No search results")
                self.empty_lbl.configure(
                    text="No files match your search.",
                )
            elif status_filter:
                title = {
                    "all": "No files added yet",
                    "ready": "Nothing ready",
                    "review": "Nothing to review",
                    "nomatch": "No files without a match",
                    "error": "No errors",
                    "done": "Nothing completed",
                }[status_filter]
                self.empty_title_var.set(title)
                self.empty_lbl.configure(
                    text="No files match this filter.",
                )
            else:
                self.empty_title_var.set("No files added yet")
                self.empty_lbl.configure(
                    text="Click ‘Add Files’ or ‘Add Folder’ or Drag & Drop "
                         "files here to get started.",
                )
            self.empty_state.place(relx=0.5, rely=0.5, anchor="center")

    @staticmethod
    def needs_manual_review(item):
        return item.status in MANUAL_REVIEW_STATUSES

    @staticmethod
    def _matches_status_filter(item, selected_filter):
        return matches_list_filter(item, status_filter=selected_filter)

    def _matches_current_filter(self, item, query=None):
        if query is None:
            query = getattr(self, "_search_query", "")
        original_name = getattr(item, "orig_path", getattr(item, "path", ""))
        if original_name is None:
            original_name = ""
        if isinstance(original_name, (str, Path)):
            original_name = Path(original_name).name if str(original_name) else ""
        else:
            original_name = str(original_name)
        return matches_list_filter(
            item,
            status_filter=getattr(self, "_status_filter", None),
            media_mode=getattr(self, "media_mode", None).get()
            if hasattr(self, "media_mode") else None,
            query=query,
            original_name=original_name,
            new_name=getattr(self, "_search_new_names", {}).get(item.iid, ""),
        )

    def _search_changed(self, query):
        if self._search_after is not None:
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(
            150, lambda value=query: self._apply_search_query(value),
        )

    def _apply_search_query(self, query):
        self._search_after = None
        self._search_query = query
        for item in self.items:
            self._sync_row_visibility(item)
        self._restore_visible_row_order()
        self.update_summary()

    def _resize_search_box(self, event=None):
        if not hasattr(self, "search_box"):
            return
        scale = self.UI_SCALE
        available = (
            event.width if event is not None
            else self.search_slot.winfo_width()
        )
        minimum = (
            self.header_layout.search_min_width
            if hasattr(self, "header_layout") else round(200 * scale)
        )
        maximum = round(SEARCH_MAX_WIDTH * scale)
        self.search_box.set_width(max(minimum, min(maximum, available)))

    def focus_search(self):
        if not self.page_main.winfo_ismapped():
            return None
        self.search_box.focus()
        return "break"

    def _action_scope_items(self):
        selected = self.selected()
        if selected:
            return selected
        if self._status_filter:
            visible_iids = set(self._visible_item_iids())
            return [item for item in self.items if item.iid in visible_iids]
        return list(self.items)

    def toggle_review_filter(self, _event=None):
        self.set_status_filter(
            None if getattr(self, "_status_filter", None) == "review"
            else "review",
        )
        return "break"

    def set_status_filter(self, selected_filter):
        if selected_filter == "all":
            selected_filter = None
        self._status_filter = selected_filter
        self._review_filter = selected_filter == "review"
        self.deselect()
        for item in self.items:
            self._sync_row_visibility(item)
        self._restore_visible_row_order()
        self.update_summary()
        self._schedule_status_cells()

    def _refresh_filter_chip_styles(self):
        if not hasattr(self, "filter_control"):
            return
        selected = self._status_filter or "all"
        counts = getattr(self, "_filter_counts", {})
        self.filter_control.set_labels(tuple(
            f"{label} {counts.get(key, 0)}"
            for key, label in (
                ("all", "All"), ("ready", "Ready"), ("review", "Review"),
                ("error", "Error"), ("done", "Done"),
            )
        ))
        palette = self.pal
        colors = palette["status_colors"]
        table_colors = palette["table_chip_colors"]
        self.filter_control.set_segment_colors({
            "ready": (table_colors["ready"][1], table_colors["ready"][0]),
            "review": colors["review"],
            "error": colors["error"],
            "done": (table_colors["done"][1], table_colors["done"][0]),
        })
        self.filter_control.set_selected(selected)

    def _item_sort_key(self):
        if self.media_mode.get() == "tv":
            return lambda item: (
                (item.query or "").casefold(),
                item.season_number or 0,
                item.episode_number or 0,
                self._sort_name(item),
            )
        return lambda item: (
            (item.query or "").casefold(),
            self._sort_name(item),
        )

    def _restore_visible_row_order(self):
        if not hasattr(self, "tree"):
            return
        self.items.sort(key=self._item_sort_key())
        filtered_out = self._filtered_out
        expected = [
            item.iid for item in self.items if item.iid not in filtered_out
        ]
        get_children = getattr(self.tree, "get_children", None)
        if get_children is None or expected != list(get_children("")):
            visible_index = 0
            for item in self.items:
                if (
                    self.tree.exists(item.iid)
                    and item.iid not in self._filtered_out
                ):
                    self.tree.move(item.iid, "", visible_index)
                    visible_index += 1

    def _sort_name(self, item):
        cache = self.__dict__.setdefault("_sort_name_cache", {})
        path = item.orig_path
        name = cache.get(path)
        if name is None:
            name = cache[path] = Path(path).name.casefold()
        return name

    def _clear_review_filter(self):
        self._status_filter = None
        self._review_filter = False
        for index, item in enumerate(self.items):
            if not self.tree.exists(item.iid):
                continue
            self._sync_row_visibility(item)
        self._restore_visible_row_order()
        self._refresh_filter_chip_styles()
        self.update_summary()

    def status(self, text):
        self.status_var.set(text)

    def push_undo(self, label, undo_fn, redo_fn=None):
        self.undo_stack.append((label, undo_fn, redo_fn))
        del self.undo_stack[:-50]
        self.redo_stack.clear()
        self._update_undo_button()

    def _history_busy(self):
        return bool(self._matching_run or self.job_total or self._rename_running)

    def _update_undo_button(self):
        if not hasattr(self, "toolbar_icon_group"):
            return
        busy = self._history_busy()
        self.toolbar_icon_group.set_state(
            "undo", "normal" if self.undo_stack and not busy else "disabled",
        )
        if hasattr(self, "header_layout"):
            self.header_layout.update_overflow_menu()

    def _capture_record(self, item, index=None):
        if index is None:
            try:
                index = self.items.index(item)
            except ValueError:
                index = len(self.items)
        fields = (
            "path", "root", "status", "match", "status_detail",
            "error_code", "error", "review_reason", "show_result",
            "_no_change_origin", "match_source", "_pending_manual_show",
            "match_method", "tried_sources", "title_score",
        )
        state = {}
        for name in fields:
            if not hasattr(item, name):
                continue
            value = getattr(item, name)
            if isinstance(value, tk.Misc):
                continue
            state[name] = copy.deepcopy(value)
        values = ()
        if item.iid and self.tree.exists(item.iid):
            values = self.tree.item(item.iid, "values")
        paths = copy.deepcopy(self._row_tooltip_paths.get(item.iid, {}))
        return {
            "item": item,
            "index": index,
            "state": state,
            "new_name": values[1] if len(values) > 1 else "",
            "target_path": paths.get("#2", ""),
            "row_paths": paths,
        }

    def _capture_records(self, items):
        positions = {id(row): n for n, row in enumerate(self.items)}
        return [
            self._capture_record(item, positions.get(id(item)))
            for item in items
        ]

    def _prepare_history_mode(self, mode):
        if mode == self.media_mode.get():
            return
        self.clear(record=False)
        self.media_mode.set(mode)
        self.update_media_mode_selector()
        self.save_config()

    def _restore_records(self, records, replace=False):
        if not records:
            return
        mode = records[0]["item"].media_type
        if mode in ("movie", "tv"):
            self._prepare_history_mode(mode)
        if replace:
            records = sorted(records, key=lambda record: record["index"])
            self.items = [record["item"] for record in records]
        else:
            for record in sorted(records, key=lambda value: value["index"]):
                item = record["item"]
                if item not in self.items:
                    self.items.insert(
                        min(record["index"], len(self.items)), item,
                    )
        for record in records:
            item = record["item"]
            for name in (
                "path", "root", "status", "match", "status_detail",
                "error_code", "error", "review_reason", "show_result",
                "_no_change_origin", "match_source", "_pending_manual_show",
                "match_method", "tried_sources", "title_score",
            ):
                if name in record["state"]:
                    setattr(item, name, copy.deepcopy(record["state"][name]))
                elif hasattr(item, name):
                    delattr(item, name)
            item.iid = None
        self._rebuild_tree()

    def _remove_items_from_list(self, items):
        removed = set(items)
        self.items = [item for item in self.items if item not in removed]
        self._rebuild_tree()

    def _rebuild_tree(self):
        row_ids = set(self.tree.get_children()) | self._filtered_out
        if row_ids:
            self.tree.delete(*row_ids)
        self.by_iid.clear()
        self._filtered_out.clear()
        self._row_tooltip_paths.clear()
        self._search_new_names.clear()
        for item in self.items:
            item.iid = self.tree.insert("", "end", values=("", "", ""))
            self.by_iid[item.iid] = item
            self.refresh_row(item)
        self._restore_visible_row_order()
        self._vcount.clear()
        self.schedule_summary()
        self.update_action_labels()
        self._update_undo_button()

    def _refresh_after_history(self):
        self.tree.selection_remove(self.tree.selection())
        self.refresh_all()
        self.schedule_summary()
        self.update_action_labels()
        self._update_undo_button()

    def undo(self):
        if self._history_busy():
            self.status("Wait for the current operation to finish.")
            return
        if not self.undo_stack:
            return
        label, undo_fn, redo_fn = self.undo_stack.pop()
        try:
            failures = undo_fn() or []
        except Exception as error:
            logging.getLogger(__name__).exception("Undo failed.")
            self.undo_stack.append((label, undo_fn, redo_fn))
            self._update_undo_button()
            show_error(self, "Undo failed", str(error))
            return
        if redo_fn is not None:
            self.redo_stack.append((label, undo_fn, redo_fn))
            del self.redo_stack[:-50]
        else:
            self.redo_stack.clear()
        self._refresh_after_history()
        self.status(f"Undid {label}.")
        if failures:
            show_warning(
                self, "Undo incomplete",
                "Some files could not be restored:\n"
                + "\n".join(str(failure) for failure in failures[:8]),
            )

    def redo(self):
        if self._history_busy():
            self.status("Wait for the current operation to finish.")
            return
        if not self.redo_stack:
            return
        label, undo_fn, redo_fn = self.redo_stack.pop()
        if redo_fn is None:
            self._update_undo_button()
            return
        try:
            redo_fn()
        except Exception as error:
            logging.getLogger(__name__).exception("Redo failed.")
            self.redo_stack.append((label, undo_fn, redo_fn))
            show_error(self, "Redo failed", str(error))
            return
        self.undo_stack.append((label, undo_fn, redo_fn))
        del self.undo_stack[:-50]
        self._refresh_after_history()
        self.status(f"Redid {label}.")
        self._update_undo_button()

    def _run_main_shortcut(self, event, action):
        if action == "focus_search":
            return self.focus_search()
        focused = self.root.focus_get()
        if focused and focused.winfo_class() in {
            "Entry", "TEntry", "Text", "Spinbox", "TSpinbox", "TCombobox",
        }:
            return None
        if action == "undo" and event.state & 0x0001:
            action = "redo"
        getattr(self, action)()
        return "break"

    # ---- API --------------------------------------------------------------
    def tmdb(self, operation_cache=None):
        """Return the metadata client using the selected source or priority chain."""
        return ProviderRouter(
            self.provider_settings, self.key_var.get().strip(),
            self.language(), operation_cache=operation_cache,
        )

    def set_provider_settings(self, settings):
        """Apply new provider choices (order, on/off, keys) and save them."""
        self.provider_settings = settings
        self.settings_store.update(settings.to_config())
        self._configure_resolver()
        if hasattr(self, "source_control"):
            self.source_control.set_selected(settings.selected)
            self.refresh_source_control()

    def refresh_source_control(self):
        if not hasattr(self, "source_control"):
            return
        self.source_control.set_available({
            name: self.provider_settings.can_select(
                name, self.key_var.get(),
            )
            for name in SOURCE_SELECTIONS
        })

    def select_source(self, name):
        if not self.provider_settings.can_select(name, self.key_var.get()):
            self.show_page("settings")
            self.settings_view.focus_provider_source(name)
            return
        self.set_provider_settings(
            self.provider_settings.with_selected(name),
        )

    def _configure_resolver(self):
        self.show_resolver.configure(
            self.key_var.get().strip(), self.language(),
            self.provider_settings,
        )

    def resolve_show(self, title, year=None, operation_cache=None):
        return self.show_resolver.resolve_show(
            title, year, operation_cache=operation_cache,
        )

    def remember_show(self, item, choice, details):
        self._configure_resolver()
        title = item.query or choice.get("title")
        result = self.show_resolver.remember(
            title,
            choice["id"],
            choice.get("title"),
            choice.get("year"),
            details,
        )
        item.show_result = result


    def test_key(self):
        self.settings_view.test_key()

    # ---- adding files -----------------------------------------------------
    def on_drop(self, event):
        self.add_paths(self.root.tk.splitlist(event.data))
        return event.action

    def add_files(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT))
        paths = filedialog.askopenfilenames(
            title="Choose TV episode files" if self.media_mode.get() == "tv"
            else "Choose movie files",
            filetypes=[("Video files", exts), ("All files", "*.*")],
            initialdir=self._dialog_initialdir(),
        )
        if paths:
            self._remember_dialog_folder(Path(paths[0]).parent)
            self.add_paths(paths)

    def add_folder(self):
        title = (
            "Choose a TV shows folder"
            if self.media_mode.get() == "tv" else "Choose a movies folder"
        )
        p = filedialog.askdirectory(
            title=title, initialdir=self._dialog_initialdir(),
        )
        if p:
            self._remember_dialog_folder(p)
            self.add_paths([p])

    def _dialog_initialdir(self):
        folder = Path(self._last_folder) if self._last_folder else None
        if folder is not None and folder.is_dir():
            return str(folder)
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            return str(downloads)
        return str(Path.home())

    def _remember_dialog_folder(self, folder):
        folder = Path(folder)
        if folder.is_dir():
            self._last_folder = str(folder)
            self.settings_store.set("last_folder", self._last_folder)

    def update_media_mode_selector(self):
        self.media_mode_selector.set_mode(self.media_mode.get())

    def switch_media_mode(self, target):
        if target == self.media_mode.get():
            return
        if self.job_total:
            show_info(
                self,
                "Matching in progress",
                "Wait for the current matching run to finish before "
                "switching media mode.",
            )
            return
        if self.items and not ask_yes_no(
            self,
            "Switch media mode",
            "Switching mode will clear the current file list. "
            "Files already applied will not be changed. Continue?",
        ):
            return
        if self.items:
            self.clear(record=False)
        self.media_mode.set(target)
        self.update_media_mode_selector()
        self.save_config()
        mode_name = "TV Shows" if self.media_mode.get() == "tv" else "Movies"
        self.status(f"{mode_name} mode selected.")

    def _confirm_detected_media_mode(self, paths):
        future = self._worker_pool.submit(
            _detect_media_mode, paths, self.media_mode.get(),
        )
        future.add_done_callback(
            lambda done: self.root.after(
                0,
                self._finish_detected_media_mode,
                done,
            ),
        )

    def _finish_detected_media_mode(self, future):
        if not getattr(self.root, "winfo_exists", lambda: True)():
            return
        try:
            detected_mode = future.result()
        except Exception:
            return
        if detected_mode:
            self._ask_switch_media_mode(detected_mode)

    def _ask_switch_media_mode(self, detected_mode):
        """Ask whether to switch to the detected mode; True when switched."""
        current_mode = self.media_mode.get()
        current_name = "TV Shows" if current_mode == "tv" else "Movies"
        detected_name = "TV Shows" if detected_mode == "tv" else "Movies"
        message = (
            f"These filenames look like {detected_name}, but Moviq is in "
            f"{current_name} mode."
        )
        if self.items:
            message += " Switching modes will clear the current file list."
        message += (
            f" Switch to {detected_name} and add these files? Click No to "
            f"ignore this suggestion and keep {current_name} mode."
        )
        if not ask_yes_no(self, "Check media mode", message):
            return False

        if self.items:
            self.clear(record=False)
        self.media_mode.set(detected_mode)
        self.update_media_mode_selector()
        self.save_config()
        return True

    def add_paths(self, paths):
        """Add files/folders without freezing the window.

        Scanning runs in a worker thread; rows are then created in short
        slices on the Tk thread so the UI keeps repainting.
        """
        paths = list(paths)
        if paths and self._matching_run:
            self.cancel_matching()
        if not paths:
            return
        if getattr(self, "_import_job", None):
            self._import_waiting = getattr(self, "_import_waiting", [])
            self._import_waiting.append(paths)
            self.status("Still adding files. These will be added next.")
            return
        missing, unavailable = self._prune_missing_items()
        job = {
            "paths": paths,
            "known": {i.path for i in self.items},
            "media_type": self.media_mode.get(),
            "missing": missing,
            "unavailable": unavailable,
            "scanned": {},
            "detect": True,
            "entries": [],
            "pos": 0,
            "built": [],
            "insert_pos": 0,
            "new": [],
            "invalid_tv": 0,
            "filter_reset": False,
            "cancel": False,
        }
        self._import_job = job
        self.status("Scanning files...")
        self._import_start_worker(job)

    def _import_start_worker(self, job):
        job["done"] = False
        job["error"] = None
        thread = threading.Thread(
            target=self._import_worker, args=(job,), daemon=True,
        )
        job["thread"] = thread
        thread.start()
        self.root.after(30, self._import_wait_scan, job)

    @staticmethod
    def _import_worker(job):
        """Filesystem work only. Never touches Tk widgets or variables."""
        try:
            media_type = job["media_type"]
            scanned = job["scanned"]
            if job["detect"]:
                files = []
                for raw in job["paths"]:
                    path = Path(raw)
                    if path.is_dir():
                        if raw not in scanned:
                            scanned[raw] = scan_dir(path)
                        files.extend(
                            (file_path, path) for file_path in scanned[raw]
                        )
                    elif path.suffix.lower() in VIDEO_EXT:
                        files.append((path, path.parent))
                job["detected"] = _detect_mode_from_files(files, media_type)
                if job["detected"]:
                    return
            known = set(job["known"])
            entries = []
            for raw in job["paths"]:
                p = Path(raw)
                if is_hidden_path(p):
                    continue
                if p.is_dir():
                    if raw not in scanned:
                        scanned[raw] = scan_dir(p)
                    files = scanned[raw]
                    item_root = (
                        tv_root_for_folder(p, files)
                        if media_type == "tv" else p
                    )
                    for f in files:
                        if f not in known:
                            entries.append((f, item_root))
                            known.add(f)
                elif (
                    p.is_file()
                    and p.suffix.lower() in VIDEO_EXT
                    and p not in known
                ):
                    entries.append((p, p.parent))
                    known.add(p)
            job["entries"] = entries
        except Exception as error:  # reported on the Tk thread
            job["error"] = error
        finally:
            job["done"] = True

    def _import_wait_scan(self, job):
        if self._import_job is not job:
            return
        if job["cancel"]:
            self._import_abort()
            return
        if not job["done"]:
            self.root.after(30, self._import_wait_scan, job)
            return
        if job["error"] is not None:
            error = job["error"]
            self._import_abort()
            self.status(f"Could not read the files: {error}")
            return
        detected = job.pop("detected", None)
        if job["detect"] and detected:
            switched = self._ask_switch_media_mode(detected)
            if self._import_job is not job:
                return
            if switched:
                job["media_type"] = self.media_mode.get()
                job["known"] = {i.path for i in self.items}
            job["detect"] = False
            self.status("Scanning files...")
            self._import_start_worker(job)
            return
        job["detect"] = False
        if job["entries"]:
            self.set_status_filter("all")
        self._import_build(job)

    def _import_make_item(self, job, f, item_root):
        media_type = job["media_type"]
        item = Item(f, item_root, media_type)
        if media_type == "tv" and not item.is_tv_episode:
            item.status = "error"
            item.error_code = "wrong_mode_movie_in_tv"
            item.error = (
                "TV filename does not include a recognized season "
                "and episode number."
            )
            job["invalid_tv"] += 1
        elif media_type == "movie":
            _title, _year, season, episode = parse_tv_name(f)
            if (
                has_explicit_episode_marker(f)
                and season is not None
                and episode is not None
            ):
                item.status = "error"
                item.error_code = "wrong_mode_tv_in_movies"
                item.episode_code = f"S{season:02d}E{episode:02d}"
        return item

    def _import_build(self, job):
        """Parse file names in short slices (no Tk work)."""
        if self._import_job is not job:
            return
        if job["cancel"]:
            self._import_abort()
            return
        entries = job["entries"]
        deadline = time.perf_counter() + 0.012
        while job["pos"] < len(entries) and time.perf_counter() < deadline:
            f, item_root = entries[job["pos"]]
            job["pos"] += 1
            job["built"].append(self._import_make_item(job, f, item_root))
        if job["pos"] < len(entries):
            self.status(
                f"Reading files... {job['pos']:,} of {len(entries):,}"
            )
            self.root.update_idletasks()
            self.root.after(6, self._import_build, job)
            return
        job["built"].sort(key=self._item_sort_key())
        self._import_insert(job)

    def _import_insert(self, job):
        """Add rows in final sorted order so nothing has to be moved later."""
        if self._import_job is not job:
            return
        if job["cancel"]:
            self._import_abort()
            return
        built = job["built"]
        deadline = time.perf_counter() + 0.015
        with self._bulk_refresh():
            while job["insert_pos"] < len(built) and time.perf_counter() < deadline:
                it = built[job["insert_pos"]]
                job["insert_pos"] += 1
                it.iid = self.tree.insert("", "end", values=("", "", ""))
                self.by_iid[it.iid] = it
                self.items.append(it)
                self.refresh_row(it)
                job["new"].append(it)
        if job["insert_pos"] < len(built):
            self.status(
                f"Adding files... {job['insert_pos']:,} of {len(built):,}"
            )
            self.root.update_idletasks()
            self.root.after(6, self._import_insert, job)
            return
        self.items.sort(key=self._item_sort_key())
        expected = [
            it.iid for it in self.items if it.iid not in self._filtered_out
        ]
        job["reorder_pos"] = 0
        job["reorder_index"] = 0
        if expected == list(self.tree.get_children("")):
            job["reorder_pos"] = len(self.items)
        self._import_reorder(job)

    def _import_reorder(self, job):
        """Only needed when new rows interleave with rows added earlier."""
        if self._import_job is not job:
            return
        deadline = time.perf_counter() + 0.015
        while job["reorder_pos"] < len(self.items) and time.perf_counter() < deadline:
            it = self.items[job["reorder_pos"]]
            job["reorder_pos"] += 1
            if self.tree.exists(it.iid) and it.iid not in self._filtered_out:
                self.tree.move(it.iid, "", job["reorder_index"])
                job["reorder_index"] += 1
        if job["reorder_pos"] < len(self.items):
            self.root.after(6, self._import_reorder, job)
            return
        self._import_finish(job)

    def _import_abort(self):
        self._import_job = None
        self._import_waiting = []

    def _import_finish(self, job):
        paths = job["paths"]
        new = [it for it in job["new"] if it.iid in self.by_iid]
        missing = job["missing"]
        unavailable = job["unavailable"]
        invalid_tv = job["invalid_tv"]
        self._schedule_status_cells()
        self._vcount.clear()
        status_message = f"Added {len(new)} video file(s)."
        if not new and any(Path(raw).is_dir() for raw in paths):
            status_message += (
                " No video files found in the folder (small files named "
                "sample or trailer are skipped)."
            )
        if missing:
            status_message += f" Removed {len(missing)} missing source file(s)."
        if unavailable:
            status_message += (
                f" Could not verify {len(unavailable)} existing source "
                "file(s)."
            )
        if invalid_tv:
            status_message += (
                f" {invalid_tv} TV file(s) need recognized season and "
                "episode numbers."
            )
        self.status(status_message)
        job["new"] = new
        job["records"] = []
        job["positions"] = {id(it): n for n, it in enumerate(self.items)}
        job["capture_pos"] = 0
        self._import_capture(job)

    def _import_capture(self, job):
        """Capture undo records in slices (deep copies are slow in bulk)."""
        if self._import_job is not job:
            return
        new = job["new"]
        deadline = time.perf_counter() + 0.015
        while job["capture_pos"] < len(new) and time.perf_counter() < deadline:
            it = new[job["capture_pos"]]
            job["capture_pos"] += 1
            job["records"].append(
                self._capture_record(it, job["positions"].get(id(it)))
            )
        if job["capture_pos"] < len(new):
            self.root.after(6, self._import_capture, job)
            return
        self._import_complete(job)

    def _import_complete(self, job):
        new = job["new"]
        self._import_job = None
        if new:
            added_records = job["records"]

            def undo_add(records=added_records):
                self._remove_items_from_list(
                    record["item"] for record in records
                )

            def redo_add(records=added_records):
                self._restore_records(records)

            self.push_undo(
                f"Add files ({len(new)} files)", undo_add, redo_add,
            )
        matchable = [
            item for item in new
            if (
                (item.media_type != "tv" or item.is_tv_episode)
                and item.error_code not in (
                    "wrong_mode_movie_in_tv", "wrong_mode_tv_in_movies",
                )
            )
        ]
        if matchable and self.auto_match.get():
            self.start_match(matchable)
        waiting = getattr(self, "_import_waiting", [])
        if waiting:
            self.add_paths(waiting.pop(0))

    def _prune_missing_items(self):
        missing = []
        unavailable = []
        for item in tuple(self.items):
            if is_hidden_path(item.path):
                missing.append(item)
                continue
            try:
                mode = item.path.stat().st_mode
            except FileNotFoundError:
                missing.append(item)
            except OSError as error:
                unavailable.append((item, error))
            else:
                if not stat.S_ISREG(mode):
                    missing.append(item)
        for item in missing:
            if self.tree.exists(item.iid):
                self.tree.delete(item.iid)
            self.by_iid.pop(item.iid, None)
            self._filtered_out.discard(item.iid)
            self._row_tooltip_paths.pop(item.iid, None)
            search_new_names = getattr(self, "_search_new_names", None)
            if search_new_names is not None:
                search_new_names.pop(item.iid, None)
            self.items.remove(item)
        for item, error in unavailable:
            item.status = "error"
            item.error_code = self._error_code_for_exception(error)
            item.error = f"Could not verify source file: {error}"
            self.refresh_row(item)
        if missing:
            self.schedule_summary()
        return missing, unavailable

    # ---- rows -------------------------------------------------------------
    def nvideos(self, folder):
        if folder not in self._vcount:
            self._vcount[folder] = count_videos(folder)
        return self._vcount[folder]

    def plan_for(self, item, choice=None):
        choice = choice or item.match
        if not choice:
            raise ValueError("File has not been matched.")
        if item.media_type == "tv":
            show_folder = format_pattern(
                self.tv_show_folder_pattern.get(), choice, "{title}",
            )
            season_folder = format_pattern(
                self.tv_season_folder_pattern.get(), choice,
                "Season {season:00}",
            )
            file_stem = format_pattern(
                self.tv_template.get(), choice,
                "{title} - S{season:00}E{episode:00} - {episode_title}",
            )
            plan = make_tv_plan(
                item.path, item.root, choice,
                show_folder=show_folder,
                season_folder=season_folder,
                file_stem=file_stem,
                create_show_folder=self.tv_create_show_folder.get(),
                create_season_folder=self.tv_create_season_folder.get(),
            )
        else:
            movie_folder = format_pattern(
                self.movie_folder_pattern.get(), choice,
                "{title} ({year})",
            )
            file_stem = format_pattern(
                self.name_template.get(), choice, "{title} ({year})",
            )
            plan = make_plan(
                item.path, item.root, choice, self.movie_folder.get(),
                self.movie_folder.get(), True,
                self.nvideos(item.path.parent), folder_name=movie_folder,
                file_stem=file_stem,
            )
        if self.library_mode.get() == "library":
            library = Path(
                self.tv_library.get() if item.media_type == "tv"
                else self.movies_library.get()
            )
            if item.media_type == "tv":
                relative_parts = []
                if self.tv_create_show_folder.get():
                    relative_parts.append(show_folder)
                if self.tv_create_season_folder.get():
                    relative_parts.append(season_folder)
                relative_parts.append(file_stem + item.path.suffix)
                plan.dest = library.joinpath(*relative_parts)
                plan.tv_show_dir = (
                    library / show_folder
                    if self.tv_create_show_folder.get() else library
                )
            else:
                relative_parts = (
                    [movie_folder] if self.movie_folder.get() else []
                )
                relative_parts.append(file_stem + item.path.suffix)
                plan.dest = library.joinpath(*relative_parts)
            plan.dest_dir = plan.dest.parent
            plan.rename_dir = None
            plan.cleanup_root = None
            plan.preserve_source_dirs = True
        return plan

    def _library_root_for(self, media_type):
        configured = (
            self.tv_library.get() if media_type == "tv"
            else self.movies_library.get()
        ).strip()
        if not configured:
            label = "TV library folder" if media_type == "tv" else (
                "Movies library folder"
            )
            raise ValueError(
                f"Choose a {label} in Settings > Files & Safety before "
                "renaming."
            )
        root = Path(
            configured
        )
        label = "TV library folder" if media_type == "tv" else (
            "Movies library folder"
        )
        if not root.is_dir():
            raise ValueError(
                f"Move to library is enabled, but the {label} is missing. "
                "Choose an existing folder in Settings > Files & Safety."
            )
        if not os.access(root, os.W_OK | os.X_OK):
            raise ValueError(
                f"Moviq cannot write to the {label}. Choose a writable "
                "folder in Settings > Files & Safety."
            )
        return root

    def _is_no_change(self, item):
        if not item.match:
            return False
        try:
            return self._normalized_path(self.plan_for(item).dest) == (
                self._normalized_path(item.path)
            )
        except ValueError:
            return False

    @staticmethod
    def _normalized_path(path):
        path = os.path.abspath(os.fspath(path))
        return os.path.normcase(os.path.normpath(path))

    def _update_no_change_status(self, item):
        if item.status not in ("auto", "manual", "no_change"):
            return
        if self._is_no_change(item):
            if item.status != "no_change":
                item._no_change_origin = item.status
            item.status = "no_change"
        elif item.status == "no_change":
            default_origin = (
                "manual"
                if getattr(item, "match_source", None) == "manual"
                else "auto"
            )
            item.status = getattr(
                item, "_no_change_origin", default_origin,
            )

    def refresh_row(self, item):
        self.schedule_summary()
        self._hide_tree_tooltip()
        self._update_no_change_status(item)
        new = ""
        new_path = ""
        output_match = item.match
        if not output_match and item.status == "review":
            output_match = getattr(item, "review_match", None)
        preview_match = output_match
        if (
            preview_match and not item.match and item.media_type == "tv"
            and not (
                isinstance(preview_match.get("season_number"), int)
                and isinstance(preview_match.get("episode_number"), int)
            )
        ):
            preview_match = None
        if preview_match:
            try:
                if item.match:
                    target = (
                        item.path if item.status == "done"
                        else self.plan_for(item).dest
                    )
                else:
                    target = self.plan_for(item, preview_match).dest
            except ValueError:
                pass
            else:
                new_path = str(target)
                new = target.name
        status_tooltip = ""
        if item.status == "review" and output_match:
            confidence = (
                tv_score(output_match, item.query)
                if item.media_type == "tv"
                else score(output_match, item.query)
            )
            if confidence >= 0.9 or getattr(item, "review_reason", None) in (
                "year_title", "year", "similar", "offline",
            ):
                status_tooltip = "Needs a check"
            else:
                status_tooltip = f"Confidence: {confidence:.0%}"
        self._row_tooltip_paths[item.iid] = {
            "#0": str(Path(item.orig_path).absolute()),
            "#1": status_tooltip,
            "#2": str(Path(new_path).absolute()) if new_path else "",
            "display": (
                self._destination_display(item, new_path)
                if new_path else new
            ),
        }
        search_new_name = new
        item.status_detail = self._status_detail(item, new_path)
        original = Path(item.orig_path).name
        status, _status_color = self._table_status_chip(item)
        if item.status == "nomatch":
            new = "No result found"
        elif item.status == "matching":
            new = "Matching..."
        self.tree.item(
            item.iid, text=original,
            values=(status, new, self._table_source_label(item)),
        )
        self._search_new_names[item.iid] = search_new_name
        self._schedule_status_cells()
        self._sync_row_visibility(item)

    def _sync_row_visibility(self, item):
        if not self.tree.exists(item.iid):
            return
        should_show = self._matches_current_filter(item)
        is_hidden = item.iid in self._filtered_out
        if should_show and is_hidden:
            self.tree.reattach(item.iid, "", "end")
            self._filtered_out.remove(item.iid)
        elif not should_show and not is_hidden:
            self.tree.detach(item.iid)
            self._filtered_out.add(item.iid)

    def on_tree_hover(self, event):
        if self.tree.identify_region(event.x, event.y) in (
            "separator", "scrollbar",
        ):
            self.tree.configure(cursor="arrow")
            return
        iid = self.tree.identify_row(event.y)
        item = self.by_iid.get(iid)
        self.tree.configure(
            cursor=(
                "hand2"
                if item
                and item.status != "done"
                and self._is_manual_match_cell(iid, event.x)
                else "arrow"
            ),
        )
        self._schedule_status_cells()

    def clear_tree_hover(self, _event=None):
        self.tree.configure(cursor="arrow")
        self._schedule_status_cells()

    @contextlib.contextmanager
    def _bulk_refresh(self):
        """Cache the tree selection while many rows are refreshed at once."""
        previous = getattr(self, "_bulk_selection", None)
        tree = getattr(self, "tree", None)
        self._bulk_selection = (
            set(tree.selection()) if tree is not None else None
        )
        if tree is not None:
            tree.begin_update()
        try:
            yield
        finally:
            self._bulk_selection = previous
            if tree is not None:
                tree.end_update()

    def refresh_all(self):
        with self._bulk_refresh():
            for it in self.items:
                self.refresh_row(it)
        self._restore_visible_row_order()
        self._schedule_status_cells()

    def selected(self):
        return [self.by_iid[i] for i in self.tree.selection() if i in self.by_iid]

    def _is_manual_match_cell(self, iid, x):
        for column in ("status", "new"):
            bounds = self.tree.bbox(iid, column)
            if bounds and bounds[0] <= x < bounds[0] + bounds[2]:
                return True
        return False

    def set_match(self, item, choice, status, source=None):
        old_match = copy.deepcopy(item.match)
        source = source or ("manual" if status == "manual" else None)
        item.match, item.status, item.error = choice, status, ""
        item.match_source = "manual" if status == "manual" else None
        item.error_code = None
        item.review_reason = None
        if hasattr(item, "_no_change_origin"):
            del item._no_change_origin
        if source == "manual":
            logging.getLogger(__name__).info(
                "Manual match changed: file=%s old_match=%r new_match=%r "
                "source=%s",
                item.path, old_match, choice, source,
            )
        self.refresh_row(item)
        self._restore_visible_row_order()

    # ---- matching (background thread) -------------------------------------
    @staticmethod
    def _auto_match_eligible(
        item, selected_source="automatic", retry_offline=False,
    ):
        media_type = getattr(item, "media_type", "movie")
        match = (
            getattr(item, "match", None)
            or getattr(item, "review_match", None)
            or {}
        )
        match_id = match.get("id")
        selected_source_mismatch = (
            selected_source != "automatic"
            and item.status in ("auto", "no_change")
            and isinstance(match_id, int)
            and not isinstance(match_id, bool)
            and provider_of(match_id) != selected_source
        )
        offline_retry = (
            retry_offline and item.status in ("auto", "no_change")
            and App._is_offline_guess(item)
        )
        return (
            getattr(item, "match_source", None) != "manual"
            and (
                item.status not in ("done", "auto", "manual", "no_change")
                or selected_source_mismatch or offline_retry
            )
            and item.error_code not in (
                "wrong_mode_movie_in_tv", "wrong_mode_tv_in_movies",
            )
            and (media_type != "tv" or getattr(item, "is_tv_episode", False))
        )

    @staticmethod
    def _is_offline_guess(item):
        match = getattr(item, "match", None)
        if not match:
            match = getattr(item, "review_match", None) or {}
        match_id = match.get("id")
        if isinstance(match_id, int) and not isinstance(match_id, bool):
            return provider_of(match_id) == "offline"
        return getattr(item, "review_reason", None) == "offline"

    @staticmethod
    def _review_reason(match, query, year, ranked, threshold, is_tv):
        match_year = match.get("year")
        if year and match_year != year:
            return "year"
        if is_tv:
            candidate_scores = [tv_score(candidate, query) for candidate in ranked]
            best_score = tv_score(match, query)
        else:
            candidate_scores = [score(candidate, query) for candidate in ranked]
            best_score = score(match, query)
        if sum(
            candidate_score >= max(threshold, best_score - 0.05)
            for candidate_score in candidate_scores
        ) > 1:
            return "similar"
        return "partial"

    def start_match(self, items, show_dialog=False, manual_kept=0):
        network_state.reset()
        items = list(items)
        active_run = self._matching_run
        run_details = (
            (active_run["id"], active_run["total"], active_run["done"])
            if active_run is not None else None
        )
        logging.getLogger("moviq.rematch").warning(
            "REMATCH: start_match items=%d matching_run=%s run_id/total/done=%s "
            "first_items=%s",
            len(items), active_run is not None, run_details,
            [
                (
                    item.status,
                    getattr(item, "review_reason", None),
                    bool(getattr(item, "match", None)),
                    bool(getattr(item, "review_match", None)),
                    self._is_offline_guess(item),
                )
                for item in items[:5]
            ],
        )
        manual_kept += sum(
            getattr(item, "match_source", None) == "manual"
            and item.status != "done"
            for item in items
        )
        if manual_kept:
            self.status(f"{manual_kept} manual matches kept.")
        items = [
            item for item in items
            if self._auto_match_eligible(
                item, self.provider_settings.selected,
                retry_offline=self._is_offline_guess(item),
            )
        ]
        logging.getLogger("moviq.rematch").warning(
            "REMATCH: eligible_items=%d", len(items),
        )
        key = self.key_var.get().strip()
        self._configure_resolver()
        if not items:
            self._export_diagnostic_report()
            return
        if items:
            self._clear_review_filter()

        run = self._matching_run
        if run is not None and (
            len(run["completed"]) >= run["total"]
            or not any(item.status == "matching" for item in run["items"])
        ):
            self._discard_match_cache(run)
            self._matching_run = None
            run = None
        if run is None:
            self._match_run_id += 1
            run = {
                "id": self._match_run_id,
                "items": [],
                "originals": {},
                "history_before": {},
                "completed": set(),
                "progress_items": set(),
                "done": 0,
                "total": 0,
                "current": None,
                "cancel_event": threading.Event(),
                "operation_cache": TMDBOperationCache(),
                "show_dialog": show_dialog,
                "manual_kept": manual_kept,
            }
            self._matching_run = run
            self.job_total = self.job_done = 0
        else:
            if show_dialog:
                run["show_dialog"] = True
            run["manual_kept"] += manual_kept

        added = False
        added_items = []
        run_item_ids = {id(queued) for queued in run["items"]}
        positions = {id(row): index for index, row in enumerate(self.items)}
        with self._bulk_refresh():
            for it in items:
                if id(it) in run_item_ids or it.status == "matching":
                    continue
                run["originals"][it] = (
                    it.status, it.match, it.error, it.error_code,
                    getattr(it, "review_reason", None),
                )
                run["history_before"][it] = self._capture_record(
                    it, positions.get(id(it), len(self.items)),
                )
                it.status, it.error = "matching", ""
                it.error_code = None
                self.refresh_row(it)
                added_items.append(it)
                run["items"].append(it)
                run_item_ids.add(id(it))
                run["total"] += 1
                self.job_total += 1
                added = True
        for _group_key, group in group_match_items(added_items):
            self.jobs.put((
                run["id"], run["cancel_event"], group, key,
                self.language(), self.threshold(), run["operation_cache"],
            ))
        if not run["total"]:
            self._matching_run = None
            return
        self._update_undo_button()
        if show_dialog:
            self._show_match_progress()
        if added:
            self._update_match_progress()

    def match_all(self):
        self.match_run()

    def _maybe_show_api_key_prompt(self):
        if self._smoke_test or self.cfg.get("api_key_prompt_seen", False):
            return True
        if (
            self.key_var.get().strip()
            or self.provider_settings.key("omdb").strip()
        ):
            return True
        self.settings_store.set("api_key_prompt_seen", True)
        self.settings_store.save_now()
        result = ApiKeyPromptDialog(self).result
        if result == "sources":
            self.show_page("settings")
            self.settings_view.focus_provider_source("tmdb")
            return False
        return result == "run"

    def match_run(self):
        """Match selected rows, or all eligible rows if nothing is selected."""
        if not self._maybe_show_api_key_prompt():
            return
        selected = self.selected()
        chosen = self._action_scope_items()
        if not selected:
            offline_items = [
                item for item in self.items
                if self._is_offline_guess(item)
            ]
            for item in offline_items:
                if item not in chosen:
                    chosen.append(item)
        self.start_match(chosen, show_dialog=True)

    def rematch_selected(self):
        self.start_match(self.selected(), show_dialog=True)

    def worker(self):
        tmdb = None

        while True:
            job = self.jobs.get()
            if job is None:
                return
            if len(job) == 6:
                run_id, cancel_event, job_items, key, lang, thr = job
                operation_cache = None
            else:
                run_id, cancel_event, job_items, key, lang, thr, operation_cache = job
            if operation_cache is None:
                operation_cache = TMDBOperationCache()
            items = (
                job_items if isinstance(job_items, (list, tuple))
                else [job_items]
            )
            if cancel_event.is_set():
                continue
            for item in items:
                self.results.put(("started", run_id, item))
            try:
                retry_offline = any(
                    self._is_offline_guess(item) for item in items
                )
                provider_settings = getattr(self, "provider_settings", None)
                if provider_settings is None:
                    if (
                        tmdb is None
                        or tmdb.key != key
                        or tmdb.language != lang
                        or tmdb.cancel_event is not cancel_event
                        or getattr(tmdb, "operation_cache", None) is not operation_cache
                    ):
                        try:
                            tmdb = TMDB(
                                key, lang,
                                cancel_event=cancel_event,
                                operation_cache=operation_cache,
                            )
                        except TypeError:
                            tmdb = TMDB(
                                key, lang,
                                cancel_event=cancel_event,
                            )
                    show_resolver = getattr(self, "show_resolver", None)
                    if show_resolver is None:
                        class _CompatShowResolver:
                            def __init__(self, client):
                                self._client = client
                                self._search_cache = {}

                            def resolve_show(
                                self, title, year=None, operation_cache=None,
                                **_kwargs,
                            ):
                                key = (title, year)
                                if key not in self._search_cache:
                                    results = self._client.search_tv(title, year)
                                    if not results:
                                        return ShowResult(ShowState.NOT_FOUND)
                                    self._search_cache[key] = results[0]
                                candidate = self._search_cache[key]
                                show_id = candidate.get("id")
                                result_year = (
                                    candidate.get("first_air_date") or ""
                                )[:4]
                                try:
                                    result_year = int(result_year)
                                except (TypeError, ValueError):
                                    result_year = None
                                return ShowResult(
                                    ShowState.RESOLVED,
                                    show_id,
                                    candidate.get("name") or candidate.get("title"),
                                    result_year,
                                    1.0,
                                    candidate=candidate,
                                )

                        if not hasattr(tmdb, "_compat_search_cache"):
                            original_search_tv = getattr(tmdb, "search_tv", None)
                            if original_search_tv is not None:
                                cache = {}
                                def search_tv_cached(query, year=None, _orig=original_search_tv, _cache=cache):
                                    key = (query, year)
                                    if key not in _cache:
                                        _cache[key] = _orig(query, year)
                                    return _cache[key]
                                tmdb.search_tv = search_tv_cached
                                tmdb._compat_search_cache = cache
                        if not hasattr(tmdb, "_compat_season_cache"):
                            original_season = getattr(tmdb, "season", None)
                            if original_season is not None:
                                cache = {}
                                def season_cached(show_id, season_number, _orig=original_season, _cache=cache):
                                    key = (show_id, season_number)
                                    if key not in _cache:
                                        _cache[key] = _orig(show_id, season_number)
                                    return _cache[key]
                                tmdb.season = season_cached
                                tmdb._compat_season_cache = cache
                        show_resolver = _CompatShowResolver(tmdb)
                        self.show_resolver = show_resolver
                    logging.getLogger("moviq.rematch").warning(
                        "REMATCH: worker job retry_offline=%s key_length=%d "
                        "selected=%s",
                        retry_offline, len(key), "compat",
                    )
                    selected_source = None
                    selected_provider = None
                else:
                    if (
                        tmdb is None
                        or tmdb.key != key
                        or tmdb.language != lang
                        or tmdb.cancel_event is not cancel_event
                        or tmdb.operation_cache is not operation_cache
                        or tmdb.settings != provider_settings
                        or tmdb.retry_offline != retry_offline
                    ):
                        tmdb = ProviderRouter(
                            provider_settings, key, lang,
                            cancel_event=cancel_event,
                            operation_cache=operation_cache,
                            retry_offline=retry_offline,
                        )

                    logging.getLogger("moviq.rematch").warning(
                        "REMATCH: worker job retry_offline=%s key_length=%d "
                        "selected=%s movie_chain=%s tv_chain=%s",
                        retry_offline, len(key), provider_settings.selected,
                        tmdb.chain("movie"), tmdb.chain("tv"),
                    )
                    show_resolver = self.show_resolver
                    selected_source = provider_settings.selected
                    selected_provider = (
                        tmdb._provider(selected_source)
                        if selected_source != "automatic" else None
                    )

                first_item = items[0]
                search_query = normalize_search_title(first_item.query)
                review_reason = getattr(
                    self, "_review_reason",
                    lambda *_args, **_kwargs: "review",
                )

                def report_provider(name, run_id=run_id, first_item=first_item, items=items):
                    self.results.put(("provider", run_id, first_item, name))
                    if hasattr(self, "_diagnostic_provider_used"):
                        for provider_item in items:
                            self.results.put((
                                "provider_used", run_id, provider_item, name,
                            ))

                tmdb.on_provider = report_provider

                def report_item_progress(item, run_id=run_id):
                    if hasattr(self, "root") or hasattr(self, "_match_progress"):
                        self.results.put(("item_progress", run_id, item))

                if (
                    selected_provider is not None
                    and first_item.media_type not in selected_provider.kinds
                ):
                    source_name = PROVIDER_INFO[selected_source]["label"]
                    coverage = (
                        "TV" if "tv" in selected_provider.kinds
                        else "movies"
                    )
                    message = (
                        f"{source_name} only covers {coverage}. "
                        "Pick another source in Settings > Sources."
                    )
                    results = [
                        ("error", None, message, "source_mode")
                        for _item in items
                    ]
                elif first_item.media_type == "tv":
                    results = match_tv_items(
                        tmdb, show_resolver, items, operation_cache,
                        thr, review_reason,
                        on_item_complete=report_item_progress,
                    )
                else:
                    results = match_movie_items(
                        tmdb, items, thr, search_query,
                        on_item_complete=report_item_progress,
                    )
            except TMDBAuthError as error:
                for item in items:
                    trace.trace_end(
                        lambda: str(item.path),
                        lambda: ("error", None, str(error)),
                    )
                cancel_event.set()
                self.results.put((
                    "auth_error", run_id, items[0], str(error),
                ))
                continue
            except TMDBCancelled:
                for item in items:
                    trace.trace_end(
                        lambda: str(item.path),
                        ("error", None, "cancelled"),
                    )
                continue
            except TMDBError as e:
                logging.getLogger(__name__).warning(
                    "Matching failed: %s", e,
                )
                for item in items:
                    trace.trace_end(
                        lambda: str(item.path),
                        lambda: ("error", None, str(e)),
                    )
                results = [
                    ("error", None, str(e))
                    for _item in items
                ]
            except Exception as e:
                logging.getLogger(__name__).exception(
                    "Matching worker failed.",
                )
                if sys.stderr is not None:
                    traceback.print_exc(file=sys.stderr)
                for item in items:
                    trace.trace_end(
                        lambda: str(item.path),
                        lambda: ("error", None, str(e)),
                    )
                results = [
                    ("error", None, str(e))
                    for _item in items
                ]
            if not cancel_event.is_set():
                for item, out in zip(items, results):
                    self.results.put(("finished", run_id, item, *out))

    def poll(self):
        try:
            self._poll_rename_events()
            try:
                while True:
                    _kind, is_online = self.network_events.get_nowait()
                    if hasattr(self, "settings_view"):
                        self.settings_view.on_network_change(is_online)
            except queue.Empty:
                pass
            self._ensure_match_workers()
            self._process_match_events()
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not process matching progress events.",
            )
        finally:
            rename_events = getattr(self, "_rename_events", None)
            self.root.after(
                15 if (
                    not self.results.empty()
                    or (
                        rename_events is not None
                        and not rename_events.empty()
                    )
                ) else 100, self.poll,
            )

    def _poll_rename_events(self):
        progress = self._rename_progress
        if progress is not None and progress.cancel_requested:
            self._rename_cancel_event.set()
        for _ in range(100):
            try:
                event = self._rename_events.get_nowait()
            except queue.Empty:
                return
            kind, *data = event
            if kind == "rename_progress":
                if self._rename_progress is not None:
                    self._rename_progress.update_progress(*data)
            elif kind == "folder_renamed":
                old_dir, new_dir = data
                for item in self.items:
                    item.path = rebase(item.path, old_dir, new_dir)
                    item.root = rebase(item.root, old_dir, new_dir)
            elif kind == "rename_item":
                item, new_path, error = data
                if error is not None:
                    item.error_code = self._error_code_for_exception(error)
                    item.error = self._safe_exception_message(error)
                    item.status = "error"
                else:
                    item.path, item.status, item.error = new_path, "done", ""
                    item.error_code = None
                    pending_show = getattr(item, "_pending_manual_show", None)
                    if pending_show:
                        choice, details = pending_show
                        try:
                            self.remember_show(item, choice, details)
                        except (RuntimeError, ValueError) as show_error:
                            self._rename_errors.append(
                                f"{item.path.name}: Could not remember TV "
                                f"show: {show_error}",
                            )
                        else:
                            item._pending_manual_show = None
                self.refresh_row(item)
            elif kind == "rename_complete":
                self._finish_rename(*data)

    def _finish_rename(
        self, ops, errors, renamed_count, failed_count, processed_count,
        cancelled, skipped_count, rename_total, conflict_count,
        storage_disconnected, unprocessed_count,
    ):
        errors.extend(getattr(self, "_rename_errors", ()))
        self._rename_running = False
        self._vcount.clear()
        if ops or renamed_count:
            self._push_rename_undo(
                self._rename_before, ops, renamed_count,
            )
        self.refresh_all()
        progress = self._rename_progress
        if progress is not None:
            progress.can_undo = bool(ops or renamed_count)
            progress.complete(
                conflict_count + processed_count, rename_total,
                renamed_count, skipped_count, failed_count,
                cancelled=cancelled,
            )
        if storage_disconnected:
            show_warning(
                self, "Storage disconnected",
                "Storage disconnected. Reconnect the drive and run Rename "
                f"again. {unprocessed_count} files were not processed.",
            )
        self.status(
            "Rename stopped. Completed files can be undone."
            if cancelled else "Applied."
        )
        self._update_undo_button()
        if errors:
            show_warning(
                self, "Rename completed with problems",
                "\n".join(errors[:8]),
            )
        self._rename_thread = None
        self._rename_progress = None
        self._rename_before = None
        if (
            getattr(self, "_undo_journal", None) is not None
            and not storage_disconnected
        ):
            self._undo_journal.finish()
            self._undo_journal = None

    def _ensure_match_workers(self):
        active = []
        for thread in self._worker_threads:
            if thread.is_alive():
                active.append(thread)
                continue
            if self._workers_closing:
                continue
            logging.getLogger(__name__).warning(
                "Matching worker stopped unexpectedly; restarting it.",
            )
        self._worker_threads = active
        if self._workers_closing:
            return
        for _ in range(MAX_WORKERS - len(active)):
            self._match_worker_counter += 1
            thread = threading.Thread(
                target=self._run_match_worker,
                daemon=True,
                name=f"moviq-match-{self._match_worker_counter}",
            )
            thread.start()
            self._worker_threads.append(thread)

    def _run_match_worker(self):
        try:
            self.worker()
        except Exception:
            logging.getLogger(__name__).exception(
                "Matching worker exited unexpectedly.",
            )

    def _process_match_events(self, budget=0.012):
        """Handle queued matching events for at most `budget` seconds.

        The window stays responsive because poll() calls this repeatedly
        instead of draining thousands of events in one go.
        """
        deadline = time.perf_counter() + budget
        pending = {"order": False, "progress": False}
        item_ids = None

        def flush():
            if pending["order"]:
                pending["order"] = False
                self._restore_visible_row_order()
            if pending["progress"]:
                pending["progress"] = False
                self._update_match_progress()

        try:
            with self._bulk_refresh():
                while time.perf_counter() < deadline:
                    event = self.results.get_nowait()
                    kind, run_id, item, *result = event
                    run = self._matching_run
                    if not run or run["id"] != run_id:
                        continue
                    if kind == "started":
                        run["current"] = item
                        pending["progress"] = True
                        continue
                    if kind == "provider":
                        run["current"] = item
                        run["source"] = result[0]
                        pending["progress"] = True
                        continue
                    if kind == "provider_used":
                        provider_used = getattr(
                            self, "_diagnostic_provider_used", None,
                        )
                        if provider_used is not None:
                            provider_used[str(item.path)] = result[0]
                        continue
                    if kind == "item_progress":
                        run["progress_items"].add(id(item))
                        run["current"] = item
                        pending["progress"] = True
                        continue
                    if kind == "auth_error":
                        flush()
                        self._stop_matching_for_auth(run, result[0])
                        continue
                    status, match, err, *details = result
                    item.title_score = self._status_title_score(item, match)
                    if status == "auto" and match:
                        confidence = item.title_score
                        if confidence < self.threshold():
                            status = "review"
                    review_match = match if status == "review" else None
                    if status == "review":
                        match = None
                    result_match = match or review_match
                    result_match_id = (
                        result_match.get("id")
                        if isinstance(result_match, dict) else None
                    )
                    result_provider = (
                        provider_of(result_match_id)
                        if isinstance(result_match_id, int)
                        and not isinstance(result_match_id, bool)
                        else None
                    )
                    logging.getLogger("moviq.rematch").warning(
                        "REMATCH: result path=%s status=%s provider=%s",
                        item.path.name, status, result_provider,
                    )
                    if item_ids is None:
                        item_ids = {id(row) for row in self.items}
                    if id(item) in item_ids:
                        item.review_match = review_match
                        item.status, item.match, item.error = (
                            status, match, err,
                        )
                        item.error = self._safe_exception_message(item.error)
                        item.error_code = (
                            details[0]
                            if status == "error" and details
                            and details[0] == "source_mode"
                            else "tmdb_unavailable" if status == "error"
                            else None
                        )
                        item.review_reason = (
                            details[0] if status == "review" and details
                            else None
                        )
                        self.refresh_row(item)
                        pending["order"] = True
                    run["completed"].add(item)
                    run["done"] = len(run["completed"])
                    self.job_done = run["done"]
                    pending["progress"] = True
                    if run["done"] >= run["total"]:
                        flush()
                        self._finish_match_run(run)
        except queue.Empty:
            pass
        flush()

    def _stop_matching_for_auth(self, run, message):
        self._matching_run = None
        self.job_total = self.job_done = 0
        run["cancel_event"].set()
        self._discard_match_cache(run)
        kept_jobs = []
        try:
            while True:
                job = self.jobs.get_nowait()
                if job is None or job[0] != run["id"]:
                    kept_jobs.append(job)
        except queue.Empty:
            pass
        for job in kept_jobs:
            self.jobs.put(job)

        for item in run["items"]:
            if item in run["completed"] or item.status != "matching":
                continue
            (
                item.status, item.match, _previous_error, _previous_error_code,
                _previous_review_reason,
            ) = run["originals"][item]
            item.status = "error"
            item.error_code = "tmdb_unavailable"
            item.error = ""
            run["completed"].add(item)
            if item in self.items:
                self.refresh_row(item)
        matched, flagged, failed = self._match_counts(run)
        if self._match_progress_dialog:
            self._match_progress_dialog.complete(
                run["done"], run["total"], matched, flagged, failed,
                cancelled=True,
            )
        self._record_match_history(run)
        self._update_undo_button()
        self.status("Matching stopped because the TMDB API key is invalid.")
        show_error(
            self, "Invalid TMDB API key",
            self._safe_exception_message(message),
        )

    def _show_match_progress(self):
        if self._match_progress_dialog:
            self._update_match_progress()
            return
        self._match_progress_dialog = MatchProgressPopup(
            self,
            on_cancel=self.cancel_matching,
            on_rename=lambda: self.apply(all_matched=True),
            on_review=self.show_flagged_matches,
            on_close=self._match_progress_closed,
        )
        self._update_match_progress()

    def _match_progress_closed(self, dialog):
        if self._match_progress_dialog is dialog:
            self._match_progress_dialog = None

    @staticmethod
    def _match_counts(run):
        completed = run["completed"]
        matched = sum(
            1 for item in completed
            if item.status in ("auto", "manual", "no_change")
        )
        flagged = sum(
            1 for item in completed
            if item.status == "review" and not item.error
        )
        failed = sum(
            1 for item in completed
            if item.status in ("nomatch", "error") or item.error
        )
        return matched, flagged, failed

    @staticmethod
    def _match_progress_count(run):
        return max(
            run["done"], len(run.get("progress_items", ())),
        )

    def _update_match_progress(self):
        run = self._matching_run
        if not run:
            return
        matched, flagged, failed = self._match_counts(run)
        current = run["current"]
        filename = current.path.name if current else ""
        if run["show_dialog"] and not self._match_progress_dialog:
            self._show_match_progress()
            return
        if self._match_progress_dialog:
            progress_done = self._match_progress_count(run)
            self._match_progress_dialog.update_progress(
                filename, progress_done, run["total"],
                matched, flagged, failed, source=run.get("source"),
            )
        elif run["total"]:
            progress_done = self._match_progress_count(run)
            suffix = (
                f" \u00b7 {run['manual_kept']} manual matches kept"
                if run["manual_kept"] else ""
            )
            self.status(
                f"Matching {progress_done}/{run['total']}...{suffix}",
            )

    def _finish_match_run(self, run):
        if self._matching_run is not run:
            return
        self._matching_run = None
        self.job_total = self.job_done = 0
        matched = flagged = failed = 0
        try:
            matched, flagged, failed = self._match_counts(run)
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not count completed matching results.",
            )
        try:
            if run["show_dialog"] and self._match_progress_dialog:
                self._match_progress_dialog.complete(
                    run["done"], run["total"], matched, flagged, failed,
                )
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not complete the matching progress dialog.",
            )
        try:
            message = (
                f"Matching finished: {matched} ready, {flagged} to review, "
                f"{failed} failed."
            )
            if run["manual_kept"]:
                message += f" {run['manual_kept']} manual matches kept."
            self.status(message)
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not report matching completion status.",
            )
        try:
            self._record_match_history(run)
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not record matching history.",
            )
        try:
            self._discard_match_cache(run)
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not discard matching cache.",
            )
        try:
            self._update_undo_button()
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not update the undo button after matching.",
            )
        try:
            self._export_diagnostic_report()
        except Exception:
            logging.getLogger(__name__).exception(
                "Could not export matching diagnostics.",
            )

    def _export_diagnostic_report(self):
        exporter = getattr(self, "_diagnostic_export_after_match", None)
        if exporter is None:
            return
        try:
            exporter(self)
        except Exception as error:
            logging.getLogger(__name__).exception(
                "Could not export matching diagnostics.",
            )
            self.status(f"Could not export matching diagnostics: {error}")
            show_error(self, "Export failed", str(error))

    def _record_match_history(self, run):
        items = [item for item in run["completed"] if item in self.items]
        if not items:
            return
        before = [
            run["history_before"][item]
            for item in items if item in run["history_before"]
        ]
        after = self._capture_records(items)
        label = f"Auto Match ({len(items)} files)"

        def undo_match(records=before):
            self._restore_records(records)

        def redo_match(records=after):
            self._restore_records(records)

        self.push_undo(label, undo_match, redo_match)

    @staticmethod
    def _discard_match_cache(run):
        operation_cache = run.get("operation_cache")
        if operation_cache is not None:
            operation_cache.discard()

    def cancel_matching(self):
        self._process_match_events()
        run = self._matching_run
        if not run:
            return
        self._matching_run = None
        self.job_total = self.job_done = 0
        run["cancel_event"].set()
        self._discard_match_cache(run)
        kept_jobs = []
        try:
            while True:
                job = self.jobs.get_nowait()
                if job[0] != run["id"]:
                    kept_jobs.append(job)
        except queue.Empty:
            pass
        for job in kept_jobs:
            self.jobs.put(job)

        for item in run["items"]:
            if item.status == "matching":
                (
                    item.status, item.match, item.error, item.error_code,
                    item.review_reason,
                ) = run["originals"][item]
                if item in self.items:
                    self.refresh_row(item)
        self._record_match_history(run)
        matched, flagged, failed = self._match_counts(run)
        progress_done = self._match_progress_count(run)
        if self._match_progress_dialog:
            self._match_progress_dialog.complete(
                progress_done, run["total"], matched, flagged, failed,
                cancelled=True,
            )
        self.status(
            f"Matching canceled after {progress_done} of {run['total']} files.",
        )
        self._update_undo_button()

    def show_flagged_matches(self):
        self.set_status_filter("review")
        review_items = [
            item for item in self.items
            if item.status == "review"
            and (item.media_type != "tv" or item.is_tv_episode)
            and self.tree.exists(item.iid)
        ]
        if review_items:
            self.tree.see(review_items[0].iid)
            self.tree.selection_set(review_items[0].iid)
            self.tree.focus(review_items[0].iid)
            self.on_select()
            self._start_manual_matches(review_items)

    # ---- editing ----------------------------------------------------------
    def on_click(self, event):
        """Click toggles a row; Shift+click selects a range; blank = deselect."""
        region = self.tree.identify_region(event.x, event.y)
        if region in ("separator", "scrollbar"):
            return None
        if region == "heading":
            if self.tree.identify_checkbox(event.x, event.y):
                self.toggle_all()
                return "break"
            return None
        self.tree.focus_set()
        row = self.tree.identify_row(event.y)
        if not row:
            self.deselect()
            return "break"
        if hasattr(self.tree, "focus"):
            self.tree.focus(row)
        item = self.by_iid.get(row)
        if (
            item
            and self._is_manual_match_cell(row, event.x)
            and item.status != "done"
        ):
            self.tree.selection_set(row)
            self.anchor = row
            self.fix_selected()
            return "break"
        kids = self._visible_item_iids()
        if event.state & 0x0001 and self.anchor in kids:
            a, b = sorted((kids.index(self.anchor), kids.index(row)))
            self.tree.selection_set(kids[a:b + 1])
        else:
            self.tree.selection_toggle(row)
            self.anchor = row
        return "break"

    def on_double(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region in ("separator", "scrollbar"):
            return "break"
        if hasattr(self.tree, "identify_checkbox") and self.tree.identify_checkbox(event.x, event.y):
            if region == "heading":
                self.toggle_all()
            return "break"
        if region not in ("cell", "tree"):
            return None
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            if hasattr(self.tree, "focus"):
                self.tree.focus(row)
            item = self.by_iid.get(row)
            if item and item.error:
                self.open_selected_file()
            else:
                self.fix_selected()
        return "break"

    def select_all(self):
        visible = self._visible_item_iids()
        if visible:
            self.tree.selection_set(visible)
        else:
            self.tree.selection_remove(self.tree.selection())

    def _visible_item_iids(self):
        return [
            item.iid for item in self.items
            if item.iid not in self._filtered_out
            and self.tree.exists(item.iid)
        ]

    def deselect(self):
        self.tree.selection_remove(self.tree.selection())

    def toggle_all(self):
        visible = set(self._visible_item_iids())
        selected = set(self.tree.selection())
        if visible and visible.issubset(selected):
            self.deselect()
        else:
            self.select_all()

    def on_select(self, _e=None):
        self.tree.redraw()
        self.schedule_summary()
        self._schedule_status_cells()

    def update_action_labels(self):
        selected_items = self.selected()
        selected = len(selected_items)
        total = len(getattr(self, "items", ()))
        subset_selected = 0 < selected < total
        auto_label = (
            f"Auto Match ({selected})" if subset_selected else "Auto Match"
        )
        manual_label = (
            f"Manual Match ({selected})"
            if subset_selected else "Manual Match"
        )
        if self.match_btn_var.get() != auto_label:
            self.match_btn_var.set(auto_label)
        if self.manual_match_btn_var.get() != manual_label:
            self.manual_match_btn_var.set(manual_label)
        rename_count = sum(
            1 for item in selected_items
            if item.match
            and item.status in ("auto", "manual", "review")
        )
        rename_text = (
            f"Rename ({rename_count})" if subset_selected else "Rename All"
        )
        if self.apply_btn_var.get() != rename_text:
            self.apply_btn_var.set(rename_text)

    def update_detail(self):
        sel = self.selected()
        if not sel:
            text = "No selection \u00b7 actions apply to all files"
        elif len(sel) > 1:
            text = f"{len(sel)} selected"
        else:
            it = sel[0]
            if it.error:
                text = (
                    f"{it.error}\n{it.path.absolute()}\n"
                    "Double-click to show this file in its folder."
                )
            elif not it.match:
                preview_match = getattr(it, "review_match", None)
                if not preview_match:
                    text = "No match yet for this file."
                else:
                    try:
                        destination = self.plan_for(it, preview_match).dest
                    except ValueError as error:
                        text = str(error)
                    else:
                        text = (
                            f"{Path(it.orig_path).name}  \u2192  "
                            f"{self._destination_display(it, destination)}"
                        )
            else:
                try:
                    dest = (
                        it.path if it.status == "done"
                        else self.plan_for(it).dest
                    )
                except ValueError as error:
                    text = str(error)
                else:
                    text = (
                        f"{Path(it.orig_path).name}  \u2192  "
                        f"{self._destination_display(it, dest)}"
                    )
        self.detail_var.set(text)

    def open_selected_file(self):
        sel = self.selected()
        if not sel:
            return
        path = sel[0].path
        try:
            _show_in_file_manager(path)
        except OSError as error:
            show_error(
                self, "Open file location",
                f"Could not open the file location for:\n{path.absolute()}\n\n"
                f"{error}",
            )

    def show_menu(self, event):
        self._hide_tree_tooltip()
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading":
            return "break"
        if region in ("separator", "scrollbar"):
            return "break"
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.menu.tk_popup(event.x_root, event.y_root)

    def fix_selected(self):
        sel = self.selected()
        if not sel:
            sel = [
                item for item in self.items
                if item.status != "done"
            ][:1]
            if not sel:
                self._manual_match_early_return(
                    "No files are available for Manual Match.",
                )
                return
        if sel[0].status == "done":
            show_info(self, "Already applied", "Use Undo to reverse the Rename.")
            return
        self._configure_resolver()
        self._start_manual_matches([sel[0]])

    def match_manually(self):
        if not self._maybe_show_api_key_prompt():
            return
        if self._manual_match_active:
            self._manual_match_early_return(
                "Manual Match is already in progress.",
            )
            return
        if self.job_total:
            self._manual_match_early_return(
                "Wait for automatic matching to finish before matching "
                "files manually.",
            )
            return
        visible_iids = self._visible_item_iids()
        visible_items = [
            self.by_iid[iid] for iid in visible_iids
            if iid in self.by_iid
        ]
        selected_iids = set(self.tree.selection())
        selected_visible = [
            item for item in visible_items if item.iid in selected_iids
        ]
        candidates = selected_visible or visible_items
        if selected_visible:
            queue_source = "selected"
        else:
            selected_filter = self._status_filter or "all"
            filter_labels = {
                "all": "All",
                "ready": "Ready",
                "review": "Review",
                "nomatch": "No match",
                "done": "Done",
            }
            queue_source = (
                "view(filter="
                f"{filter_labels.get(selected_filter, selected_filter)})"
            )
        self._manual_match_renamed_skipped = sum(
            item.status == "done" for item in candidates
        )
        self._manual_match_items = [
            item for item in candidates if item.status != "done"
        ]
        if self._manual_match_renamed_skipped:
            self._manual_match_early_return(
                f"{self._manual_match_renamed_skipped} renamed files "
                "skipped. Undo the rename to change them.",
            )
        if not self._manual_match_items:
            if not self._manual_match_renamed_skipped:
                self._manual_match_early_return(
                    "No files are available for Manual Match.",
                )
            return
        self._start_manual_matches(
            self._manual_match_items,
            renamed_skipped=self._manual_match_renamed_skipped,
            queue_source=queue_source,
            view_count=len(visible_items),
        )

    def _manual_match_early_return(self, reason):
        logging.getLogger(__name__).info(
            "Manual Match did not start: %s", reason,
        )
        self.status(reason)

    def _start_manual_matches(
        self, items, renamed_skipped=0, queue_source=None, view_count=None,
    ):
        if self._manual_match_active:
            self._manual_match_early_return(
                "Manual Match is already in progress.",
            )
            return
        if self.job_total:
            self._manual_match_early_return(
                "Wait for automatic matching to finish before matching "
                "files manually.",
            )
            return
        self._manual_match_items = [
            item for item in items if item.status != "done"
        ]
        self._manual_match_renamed_skipped = renamed_skipped
        if not self._manual_match_items:
            if renamed_skipped:
                self._manual_match_early_return(
                    f"{renamed_skipped} renamed files skipped. "
                    "Undo the rename to change them.",
                )
            else:
                self._manual_match_early_return(
                    "No files are available for Manual Match.",
                )
            return
        self._manual_match_queue = [
            item.iid for item in self._manual_match_items
        ]
        self._manual_match_queue_items = {
            item.iid: item for item in self._manual_match_items
        }
        if queue_source is not None:
            logging.getLogger(__name__).info(
                "Manual Match | queue=%s n=%d",
                queue_source, len(self._manual_match_queue),
            )
        self._configure_resolver()
        self._manual_match_before = self._capture_records(
            self._manual_match_items,
        )
        self._manual_match_saved = set()
        self._manual_operation_cache = TMDBOperationCache()
        self._manual_match_current = None
        self._manual_match_index = 0
        self._manual_match_active = True
        if self._status_filter and view_count is not None:
            self.status(
                f"Manual Match: {view_count} files in the current view."
            )
        self._open_next_manual_match()

    def _open_next_manual_match(self):
        while self._manual_match_index < len(self._manual_match_queue):
            item_iid = self._manual_match_queue[self._manual_match_index]
            self._manual_match_index += 1
            item = self._manual_match_queue_items[item_iid]
            self._manual_match_current = item
            try:
                SearchDialog(
                    self, item,
                    on_close=lambda outcome: self.root.after_idle(
                        lambda: self._manual_match_closed(outcome),
                    ),
                    operation_cache=self._manual_operation_cache,
                )
            except Exception:
                self._finish_manual_match()
                raise
            return

        self._finish_manual_match()

    def _manual_match_closed(self, outcome):
        if (
            outcome in ("matched", "ignored")
            and self._manual_match_current is not None
        ):
            self._manual_match_saved.add(self._manual_match_current)
        self._manual_match_current = None
        if outcome == "cancelled":
            self._finish_manual_match()
            return
        self._open_next_manual_match()

    def _finish_manual_match(self):
        try:
            saved = [
                item for item in self._manual_match_saved
                if item in self.items
            ]
            before_by_item = {
                record["item"]: record for record in self._manual_match_before
            }
            before = [
                before_by_item[item] for item in saved
                if item in before_by_item
            ]
            if saved and before:
                after = self._capture_records(saved)
                label = f"Manual Match ({len(saved)} files)"

                def undo_manual(records=before):
                    self._restore_records(records)

                def redo_manual(records=after):
                    self._restore_records(records)

                self.push_undo(label, undo_manual, redo_manual)
        finally:
            self._manual_match_items = []
            self._manual_match_queue = []
            self._manual_match_queue_items = {}
            self._manual_match_index = 0
            self._manual_match_active = False
            self._manual_match_before = []
            self._manual_match_saved = set()
            self._manual_match_current = None
            renamed_skipped = self._manual_match_renamed_skipped
            if renamed_skipped:
                self.status(
                    f"{renamed_skipped} renamed files skipped. "
                    "Undo the rename to change them.",
                )
            self._manual_match_renamed_skipped = 0
            if self._manual_operation_cache:
                self._manual_operation_cache.discard()
            self._manual_operation_cache = None
            self.schedule_summary()

    def accept_selected(self):
        for it in self.selected():
            if it.match and it.status in (
                "review", "auto", "manual", "no_change",
            ):
                self.set_match(it, it.match, "manual")

    def remove_selected(self):
        removed_items = self.selected()
        if not removed_items:
            return
        records = self._capture_records(removed_items)
        self._remove_items_from_list(removed_items)

        def undo_remove(snapshot=records):
            self._restore_records(snapshot)

        def redo_remove(snapshot=records):
            self._remove_items_from_list(
                record["item"] for record in snapshot
            )

        self.push_undo(
            f"Remove files ({len(removed_items)} files)",
            undo_remove, redo_remove,
        )

    def clear(self, record=True):
        selected_items = self.selected() if record else []
        if selected_items and len(selected_items) < len(self.items):
            self.remove_selected()
            self.status(f"Removed {len(selected_items)} files.")
            return
        job = getattr(self, "_import_job", None)
        if job is not None:
            job["cancel"] = True
            self._import_job = None
            self._import_waiting = []
        if self._matching_run:
            self.cancel_matching()
        self.set_status_filter("all")
        snapshot = self._capture_records(self.items) if record else []
        if not self.items:
            return
        self.tree.delete(*self.tree.get_children(), *self._filtered_out)
        self.items.clear()
        self.by_iid.clear()
        self._filtered_out.clear()
        self._row_tooltip_paths.clear()
        self._search_new_names.clear()
        self.schedule_summary()
        self.update_action_labels()
        self.status("List cleared.")
        if record and not self._suppress_undo_record:
            def undo_clear(records=snapshot):
                self._restore_records(records, replace=True)

            def redo_clear():
                self.clear(record=False)

            self.push_undo(
                f"Clear ({len(snapshot)} files)", undo_clear, redo_clear,
            )

    # ---- apply / undo -----------------------------------------------------
    def apply(self, all_matched=False):
        had_selection = bool(self.selected()) if not all_matched else False
        missing, unavailable = self._prune_missing_items()
        chosen = self.selected() if had_selection else []
        if missing:
            self.status(
                f"Removed {len(missing)} source file(s) that no longer exist."
            )
            if had_selection and not chosen:
                show_warning(
                    self,
                    "Source files missing",
                    "The selected source file(s) no longer exist. They were "
                    "removed from the list.",
                )
                return
        if unavailable:
            details = "\n".join(
                f"{item.path}: {error}"
                for item, error in unavailable[:5]
            )
            show_warning(
                self,
                "Could not verify source files",
                "These files were not included in this rename because Moviq "
                "could not verify that they are accessible:\n\n" + details,
            )
        unavailable_ids = {id(item) for item, _ in unavailable}
        scope = chosen if had_selection else self.items
        eligible_statuses = (
            ("auto", "manual") if all_matched or not had_selection
            else ("auto", "manual", "review")
        )
        todo = [
            item for item in scope
            if id(item) not in unavailable_ids
            and item.match
            and item.status in eligible_statuses
        ]
        if not todo:
            if (
                scope
                and all(item.status == "no_change" for item in scope)
            ):
                self.status("Nothing to rename")
                return
            show_info(
                self,
                "Nothing to do",
                "None of the selected files are matched yet." if chosen
                else "No matched files to rename yet.")
            return
        blocked_tv = [
            item for item in todo
            if item.media_type == "tv"
            and not tv_match_is_applicable(item.status, item.match)
        ]
        if blocked_tv:
            todo = [item for item in todo if item not in blocked_tv]
            show_warning(
                self,
                "TV episode needs review",
                f"{len(blocked_tv)} TV episode(s) were skipped because the "
                "show match is unconfirmed or TMDB episode information is "
                "missing. Use Search / fix match and confirm a valid episode "
                "before applying.",
            )
        if not todo:
            return
        if self.library_mode.get() == "library":
            try:
                for media_type in {item.media_type for item in todo}:
                    self._library_root_for(media_type)
            except ValueError as error:
                show_warning(self, "Library folder required", str(error))
                return

        try:
            plans = build_plans(todo, self.plan_for)
        except (OSError, ValueError, KeyError) as error:
            show_error(
                self, "Could not build rename plan",
                f"Moviq could not create the rename plan: {error}",
            )
            return

        movie_destinations = {}
        reserved_destinations = {
            self._normalized_path(plans[id(item)].dest)
            for item in todo
        }
        for item in todo:
            if item.media_type == "tv":
                continue
            plan = plans[id(item)]
            key = (
                self._normalized_path(plan.dest_dir),
                os.path.normcase(plan.new_stem),
            )
            movie_destinations.setdefault(key, []).append(item)
        for duplicates in movie_destinations.values():
            if len(duplicates) < 2:
                continue
            for item in duplicates:
                plan = plans[id(item)]
                number = 1
                while True:
                    numbered_stem = f"{plan.new_stem} ({number})"
                    destination = plan.dest_dir / (
                        numbered_stem + item.path.suffix
                    )
                    key = self._normalized_path(destination)
                    if key in reserved_destinations or destination.exists():
                        number += 1
                        continue
                    plan.new_stem = numbered_stem
                    plan.dest = destination
                    reserved_destinations.add(key)
                    break

        destinations = {}
        conflicts_by_item = {}
        for item in todo:
            destination = plans[id(item)].dest
            key = self._normalized_path(destination)
            destinations.setdefault(key, []).append(item)
        for colliding in destinations.values():
            destination = plans[id(colliding[0])].dest
            target_path = self._normalized_path(destination)
            destination_is_source = any(
                self._normalized_path(item.path) == target_path
                for item in colliding
            )
            keep_item = next(
                (
                    item for item in colliding
                    if self._normalized_path(item.path) == target_path
                ),
                colliding[0],
            )
            for item in colliding:
                if item is not keep_item or (
                    destination.exists() and not destination_is_source
                ):
                    conflicts_by_item[id(item)] = destination
        conflict_count = len(conflicts_by_item)
        skipped_count = len(scope) - len(todo)
        summary = (
            f"{len(todo)} files to rename \u00b7 {skipped_count} skipped "
            f"\u00b7 {len(conflicts_by_item)} conflicts"
        )
        if self.confirm_apply.get():
            confirmation = RenameConfirmationDialog(
                self, summary,
            )
            if not confirmation.result:
                return

        rename_before = self._capture_records(self.items)
        self._rename_running = True
        self._update_undo_button()
        if conflicts_by_item:
            for item in todo:
                destination = conflicts_by_item.get(id(item))
                if destination is None:
                    continue
                item.status = "error"
                item.error_code = "name_conflict"
                item.error = f"Destination already exists or conflicts: {destination}"
                self.refresh_row(item)
            todo = [
                item for item in todo
                if id(item) not in conflicts_by_item
            ]
        if not todo:
            self.status("No files renamed because every target conflicts.")
            self._push_rename_undo(
                rename_before, [], len(conflicts_by_item),
            )
            self._rename_running = False
            self._update_undo_button()
            return
        skipped_count = max(
            0, len(scope) - len(todo) - len(conflicts_by_item),
        )
        rename_total = len(todo) + len(conflicts_by_item)
        self._rename_before = rename_before
        self._rename_errors = []
        self._rename_cancel_event = threading.Event()
        self._rename_progress = RenameProgressPopup(
            self, on_undo=self.undo, on_cancel=self._cancel_rename,
        )
        self._undo_journal = None
        self._rename_thread = threading.Thread(
            target=self._run_rename,
            args=(
                todo,
                plans,
                self.rename_subtitles.get(),
                rename_total,
                skipped_count,
                conflict_count,
            ),
            daemon=True,
            name="moviq-rename",
        )
        self._rename_thread.start()

    def _build_rename_ops(
        self, todo, plans, rename_subtitles, cancel_event=None,
    ):
        dry_run = type(
            "DryRunOps",
            (list,),
            {"execute": lambda self, operation, action: self.append(operation)},
        )()
        for item in todo:
            if cancel_event is not None and cancel_event.is_set():
                break
            plan = plans[id(item)]
            perform_plan(
                item.path,
                plan,
                dry_run,
                rename_subtitles=rename_subtitles,
            )
        return list(dry_run)

    def _run_rename(
        self, todo, plans, rename_subtitles, rename_total, skipped_count,
        conflict_count,
    ):
        try:
            planned_ops = self._build_rename_ops(
                todo, plans, rename_subtitles, self._rename_cancel_event,
            )
            if self._rename_cancel_event.is_set():
                self._rename_events.put((
                    "rename_complete", [], [], 0, 0, 0, True,
                    skipped_count, rename_total, conflict_count, False,
                    len(todo),
                ))
                return
            undo_journal = UndoJournal()
            undo_journal.start(planned_ops)
            self._undo_journal = undo_journal
            service = RenameService(
                todo, plans, self.items, rename_subtitles,
                USER_DATA_DIR / "rename-undo.jsonl", self._rename_events,
                self._rename_cancel_event, rename_total, skipped_count,
                conflict_count, _storage_gone,
                undo_journal=undo_journal,
            )
            service.run()
        except Exception as error:
            self._rename_events.put((
                "rename_complete", [], [f"Could not prepare rename: {error}"],
                0, 1, 0, False, skipped_count, rename_total, conflict_count,
                False, len(todo),
            ))

    def _cancel_rename(self):
        self._rename_cancel_event.set()
        rename_thread = self._rename_thread
        if rename_thread is not None and rename_thread.is_alive():
            return False
        self._rename_running = False
        self._rename_thread = None
        self._rename_progress = None
        self._update_undo_button()
        return True

    def _push_rename_undo(self, records, ops, file_count):
        if not records:
            return

        def undo_rename(snapshot=records, disk_ops=ops):
            errors = undo_ops(disk_ops)
            restorable = []
            failures = []
            for record in snapshot:
                item = record["item"]
                source = Path(record["state"]["path"])
                if source.exists():
                    restorable.append(record)
                else:
                    failures.append(
                        f"{item.orig_name}: {source} could not be restored."
                    )
            if restorable:
                self._restore_records(restorable)
            self._vcount.clear()
            failures.extend(errors)
            return failures

        self.push_undo(
            f"Rename ({file_count} files)", undo_rename, None,
        )
