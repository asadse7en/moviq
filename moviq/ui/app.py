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

from .. import __version__ as APP_VERSION
from ..core.config import (
    ERROR_LOG_FILE, IMAGE_EXT, SIDECAR_EXT, VIDEO_EXT,
    remove_legacy_cache_files,
)
from ..core.file_ops import (
    count_videos, is_hidden_path, make_plan, make_tv_plan, rebase, scan_dir,
    tv_root_for_folder, undo_ops,
)
from ..core.apply import apply_item_plan, build_plans
from ..core.formatting import (
    MOVIE_DEFAULT_ID, TV_DEFAULT_ID, format_pattern,
)
from ..core.matching import (
    _is_generic_title, group_match_items, normalize_search_title, score,
    tv_match_is_applicable, tv_score,
)
from ..core.list_filter import matches_list_filter
from ..core.match_pipeline import match_movie_items, match_tv_items
from ..core.models import Item
from ..core.providers.ids import display_id, provider_of
from ..core.naming import (
    NAMING, has_explicit_episode_marker, parse_name, parse_tv_name,
)
from ..core.tmdb import (
    MAX_WORKERS, TMDB, TMDBAuthError, TMDBCancelled,
    TMDBOperationCache,
)
from ..core.providers.router import ProviderRouter
from ..core.providers.settings import (
    PROVIDER_INFO, SOURCE_SELECTIONS, ProviderSettings,
)
from ..core.show_resolver import (
    ShowResolver,
)
from .design import LANGUAGES, PALETTES
from .controls import (
    TableTooltip,
)
from .dialogs import SearchDialog
from .popups import (
    MatchProgressPopup, RenameConfirmationDialog, ask_yes_no, show_error,
    show_info, show_warning,
)
from ..core.settings_store import SettingsStore, resolve_system_theme
from .settings import SettingsView
from .table_layout import (
    target_name_display, tv_target_name_display,
)
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

from .theme import HAVE_SV, apply_theme as apply_app_theme


MANUAL_REVIEW_STATUSES = {"pending", "review", "nomatch", "error"}
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
        subprocess.Popen(["explorer.exe", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


def _detect_media_mode(paths, current_mode):
    """Infer a clear mode mismatch from episode markers or movie years."""
    files = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            files.extend(scan_dir(path))
        elif path.suffix.lower() in VIDEO_EXT:
            files.append(path)
    return _detect_mode_from_files(files, current_mode)


def _detect_mode_from_files(files, current_mode):
    episodes = 0
    movies = 0
    for path in files:
        parsed_tv = parse_tv_name(path)
        if (
            has_explicit_episode_marker(path)
            and parsed_tv[2] is not None and parsed_tv[3] is not None
        ):
            episodes += 1
        elif parse_name(path.stem)[1]:
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
    def __init__(self, root):
        self.root = root
        root.title(f"Moviq {APP_VERSION}")
        self.UI_SCALE = float(root.winfo_fpixels("1i")) / 96
        self.settings_store = SettingsStore(
            on_change=self._setting_changed,
            on_batch_done=self._flush_pending_refresh,
        )
        self.cfg = self.settings_store.data
        self._restore_window_geometry(
            self.cfg.get("window_geometry"),
            self.cfg.get("window_state", "normal"),
        )

        self.items = []
        self.by_iid = {}
        self.jobs = queue.Queue()
        self.results = queue.Queue()
        self.job_total = self.job_done = 0
        self._match_run_id = 0
        self._matching_run = None
        self._match_progress_dialog = None
        self.undo_stack = []
        self.redo_stack = []
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
        logging.getLogger().addHandler(self._file_log_handler)
        self.theme_preference = self.cfg.get("theme", "dark")
        self.mode = (
            resolve_system_theme() if self.theme_preference == "system"
            else self.theme_preference
        )
        self.pal = PALETTES[self.mode]
        self._status_tooltip = TableTooltip(
            root, self.pal.get("hover", "#f1f5f9"), self.pal["fg"],
            self.pal["chip_border"],
            self.pal["muted"],
        )
        self.have_sv = HAVE_SV
        self._toolbar_compact = False
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
        for _ in range(MAX_WORKERS):
            self._worker_pool.submit(self.worker)
        root.after(100, self.poll)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._system_theme_after = root.after(
            1000, self._poll_system_theme,
        )

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
        min_width = min(round(900 * self.UI_SCALE), screen_width)
        min_height = min(round(560 * self.UI_SCALE), screen_height)
        self.root.minsize(min_width, min_height)
        if not match:
            width = round(1180 * self.UI_SCALE)
            height = round(720 * self.UI_SCALE)
        width = min(screen_width, max(min_width, width))
        height = min(screen_height, max(min_height, height))
        x = min(max(0, x), max(0, screen_width - width))
        y = min(max(0, y), max(0, screen_height - height))
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        if sys.platform == "win32" and window_state == "zoomed":
            self.root.state("zoomed")

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
        self._hide_tree_tooltip()
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

    def apply_theme(self, mode):
        result = apply_app_theme(self, mode)
        self.update_theme_button()
        return result

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
            self.manual_match_button,
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
        self.toolbar_icon_group.set_theme(
            background=pal["header_btn"],
            border=pal["header_border"],
            hover_background=pal["header_btn_hi"],
            foreground=pal["fg"], muted=pal["header_muted"],
            canvas_background=pal["header_bg"],
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
        hover = pal.get("hover", "#f1f5f9")
        self.tree.tag_configure("hover", background=hover)
        self._status_cell_font = "TkHeadingFont"
        for label in self._status_cell_labels.values():
            label.configure(font=self._status_cell_font)
        self._schedule_status_cells()
        self.empty_icon.configure(bg=pal["card"])
        self.menu.configure(bg=pal["card"], fg=pal["fg"],
                            activebackground=pal["accent"],
                            activeforeground="#ffffff")
        if hasattr(self, "settings_view"):
            self.settings_view.recolor(pal)
        self.refresh_all()

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
        self.show_page("main" if self.key_var.get().strip() else "settings")

    def show_page(self, name):
        self.page_main.pack_forget()
        self.page_settings.pack_forget()
        (self.page_main if name == "main" else self.page_settings).pack(
            fill="both", expand=True)

    def leave_settings(self):
        try:
            self.save_config()
        except OSError as error:
            show_warning(self, "Settings", f"Could not save settings:\n{error}")
        self.show_page("main")

    def build_main_page(self, page):
        return build_app_main_page(self, page)

    def fit_table_columns(self, _event=None):
        if not hasattr(self, "_table_widths"):
            return
        if not self._table_columns_initialized:
            self._table_columns_initialized = True
            self._reset_table_widths()
            return
        if getattr(self, "_table_resize_active", False):
            return
        table_width = self.tree.winfo_width()
        previous_width = getattr(self, "_last_table_widget_width", None)
        if previous_width is not None and table_width != previous_width:
            self._table_new_autofit = False
            if not self._table_columns_customized:
                self._table_widths = self._default_column_widths()
                for column in ("#0", "status"):
                    self.tree.column(
                        column, width=self._table_widths[column],
                    )
        self._last_table_widget_width = table_width
        columns = ("#0", *self.tree["columns"])
        current = {column: int(self.tree.column(column, "width"))
                   for column in columns}
        for column in columns[:-1]:
            if current[column] != self._table_widths.get(column):
                self._table_widths[column] = max(
                    self.table_min_widths[column], current[column],
                )
        available = max(0, self.tree.winfo_width() - 2)
        fixed = sum(self._table_widths[column] for column in columns[:-1])
        if not getattr(self, "_table_new_autofit", False):
            self._table_widths["new"] = max(
                self.tree.column("new", "minwidth"), available - fixed,
            )
        changed_widths = False
        for column in columns:
            width = max(
                self.table_min_widths[column],
                self._table_widths[column],
            )
            changed_widths |= width != current[column]
            self.tree.column(column, width=width)
            self._table_widths[column] = width
        if changed_widths:
            self._queue_table_reclip()
        self._refresh_reset_columns_button()

    def _queue_table_reclip(self):
        if getattr(self, "_table_clip_after", None) is None:
            self._table_clip_after = self.root.after_idle(
                self._reclip_table_rows,
            )

    def _reclip_table_rows(self):
        self._table_clip_after = None
        with self._bulk_refresh():
            for item in self.items:
                if self.tree.exists(item.iid):
                    self.refresh_row(item)

    def _begin_table_resize(self, event):
        if self.tree.identify_region(event.x, event.y) == "separator":
            self._table_resize_active = True
            self._table_resize_column = self.tree.identify_column(event.x)

    def _finish_table_resize(self):
        self._table_resize_active = False
        self._table_new_autofit = (
            getattr(self, "_table_resize_column", None) == "#2"
        )
        self._table_resize_column = None
        for column in ("#0", *self.tree["columns"]):
            self._table_widths[column] = max(
                self.table_min_widths[column],
                int(self.tree.column(column, "width")),
            )
        defaults = self._default_column_widths()
        self._table_columns_customized = any(
            abs(self._table_widths[column] - defaults[column]) > 1
            for column in ("#0", "status")
        )
        self.fit_table_columns()

    def _default_column_widths(self):
        available = max(0, self.tree.winfo_width() - 2)
        status = self._status_column_width()
        original = max(
            self.table_min_widths["#0"],
            min(int(available * 0.4), available - status
                - self.table_min_widths["new"]),
        )
        new = max(self.table_min_widths["new"], available - original - status)
        return {"#0": original, "status": status, "new": new}

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
                count = getattr(item, "season_episode_count", 0)
                return (
                    f"Season {item.season_number} has only {count} "
                    f"episode(s) on TMDB, so episode {item.episode_number} "
                    "doesn't exist there. Check it with Manual Match."
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
                    f"Absolute episode number {item.episode_number} "
                    "couldn't be mapped."
                ),
                "episode_title": (
                    f'Episode title "{item.episode_title}" isn\'t unique '
                    f"for {item.query}."
                ),
                "episode_missing": (
                    f"Season {item.season_number} has only "
                    f"{getattr(item, 'season_episode_count', 0)} "
                    f"episode(s) on TMDB, so episode {item.episode_number} "
                    "doesn't exist there."
                ),
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

    def _status_column_width(self):
        labels = (
            "Pending", "Matched", "Confirmed", "No change", "Review",
            "No match", "Done", "Error",
        )
        font = tkfont.nametofont("TkTextFont")
        return max(110, max(font.measure(label) for label in labels) + 40)

    def _refresh_reset_columns_button(self):
        if not hasattr(self, "reset_columns_button"):
            return
        defaults = self._default_column_widths()
        differs = any(
            abs(int(self.tree.column(column, "width")) - width) > 1
            for column, width in defaults.items()
        )
        if differs and not self.reset_columns_button.winfo_manager():
            self.reset_columns_button.pack(side="right")
        elif not differs and self.reset_columns_button.winfo_manager():
            self.reset_columns_button.pack_forget()

    def _reset_table_widths(self):
        self._table_new_autofit = False
        self._table_columns_customized = False
        self._table_widths = self._default_column_widths()
        for column, width in self._table_widths.items():
            self.tree.column(column, width=width)
        self._reclip_table_rows()
        self._refresh_reset_columns_button()

    def _auto_fit_column(self, column):
        columns = ("#0", *self.tree["columns"])
        if column not in columns:
            return
        font = tkfont.nametofont("TkTextFont")
        heading = {
            "#0": "\u2610  Original name",
            "status": "Status",
            "new": "New Name",
        }[column]
        widest = font.measure(heading)
        if column == "#0":
            contents = (
                f"\u2610  {Path(item.orig_path).name}"
                for item in self.items
            )
        elif column == "status":
            contents = (
                self._table_status_label(item.status, bool(item.error))
                for item in self.items
            )
        else:
            contents = (
                self._row_tooltip_paths.get(item.iid, {}).get("display", "")
                for item in self.items
            )
        for text in contents:
            widest = max(widest, font.measure(text))
        available = max(1, int((self.tree.winfo_width() - 2) * 0.6))
        self._table_widths[column] = max(
            self.table_min_widths[column],
            min(widest + 36, available),
        )
        self.tree.column(column, width=self._table_widths[column])
        self._table_new_autofit = column == "new"
        self._table_columns_customized = True
        self.fit_table_columns()
        self._refresh_reset_columns_button()

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

    def _schedule_tree_tooltip(self, event):
        row = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        item = self.by_iid.get(row)
        if not item or column != "#1":
            self._hide_tree_tooltip()
            return
        cache_key = (row, item.status_detail, item.status, item.error_code)
        if cache_key != self._status_card_cache_key:
            card = self._status_card(item)
            self._status_card_cache = card or item.status_detail
            self._status_card_cache_key = cache_key
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
        delta = event.delta
        if abs(delta) >= 120:
            units = -int(delta / 120)
        else:
            units = -1 if delta > 0 else 1 if delta < 0 else 0
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
        if not self.tree.winfo_exists():
            return
        visible = set()
        for item in self._rows_on_screen():
            bounds = self.tree.bbox(item.iid, "status")
            if not bounds:
                continue
            x, y, width, height = bounds
            if width <= 0 or height <= 0:
                continue
            visible.add(item.iid)
            status_key = self._table_status_key(
                item.status, bool(item.error),
            )
            label = self._status_cell_labels.get(item.iid)
            if label is None:
                label = tk.Label(
                    self.tree,
                    anchor="w",
                    borderwidth=0,
                    padx=12,
                    font=self._status_cell_font,
                )
                for sequence in (
                    "<Button-1>", "<Double-1>", "<Button-3>",
                    "<Motion>", "<MouseWheel>", "<Button-4>", "<Button-5>",
                ):
                    label.bind(
                        sequence,
                        lambda event, seq=sequence:
                            self._forward_status_cell_event(event, seq),
                    )
                self._status_cell_labels[item.iid] = label
            selected = item.iid in self.tree.selection()
            hovered = "hover" in self.tree.item(item.iid, "tags")
            background = (
                self.pal.get("selection", self.pal["zone_hi"])
                if selected else
                self.pal.get("hover", "#f1f5f9")
                if hovered else self.pal["bg"]
            )
            label.configure(
                text=self._table_status_label(item.status, bool(item.error)),
                foreground=self.pal["status_text"][status_key],
                background=background,
                font=self._status_cell_font,
            )
            label.place(x=x, y=y, width=width, height=height)
        hidden = [iid for iid in self._status_cell_labels if iid not in visible]
        for position, iid in enumerate(hidden):
            label = self._status_cell_labels[iid]
            label.place_forget()
            if position >= 150 or not self.tree.exists(iid):
                label.destroy()
                del self._status_cell_labels[iid]

    def _rows_on_screen(self):
        """Rows in (or just outside) the visible part of the table."""
        if not self.tree.winfo_viewable():
            return []
        children = self.tree.get_children("")
        total = len(children)
        if total <= 300:
            rows = children
        else:
            top, bottom = self.tree.yview()
            first = max(int(top * total) - 5, 0)
            last = min(int(bottom * total) + 6, total)
            rows = children[first:min(last, first + 300)]
        return [self.by_iid[iid] for iid in rows if iid in self.by_iid]

    def _forward_status_cell_event(self, event, sequence):
        x = event.x + event.widget.winfo_rootx() - self.tree.winfo_rootx()
        y = event.y + event.widget.winfo_rooty() - self.tree.winfo_rooty()
        options = {"x": x, "y": y}
        if hasattr(event, "state"):
            options["state"] = event.state
        if sequence == "<MouseWheel>":
            options["delta"] = event.delta
        self.tree.event_generate(sequence, **options)
        return "break"

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
        c = {}
        for i in self.items:
            c[i.status] = c.get(i.status, 0) + 1
        counts = {
            "all": n,
            "ready": c.get("auto", 0) + c.get("manual", 0),
            "review": (
                c.get("review", 0) + c.get("nomatch", 0)
                + c.get("error", 0)
            ),
            "done": c.get("done", 0) + c.get("no_change", 0),
        }
        self._filter_counts = counts
        labels = {
            "all": "All", "ready": "Ready", "review": "Review",
            "done": "Done",
        }
        for key, count in counts.items():
            if hasattr(self, "filter_vars"):
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
            query = self._search_query
        return matches_list_filter(
            item,
            status_filter=getattr(self, "_status_filter", None),
            media_mode=self.media_mode.get(),
            query=query,
            original_name=Path(item.orig_path).name,
            new_name=self._search_new_names.get(item.iid, ""),
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
        self.search_box.set_width(
            min(round(420 * scale), max(round(220 * scale), available)),
        )

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
                ("done", "Done"),
            )
        ))
        self.filter_control.set_selected(selected)
        self.filter_control.set_available({
            key: counts.get(key, 0) > 0 for key in self.filter_control.values
        })

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
        if get_children is not None and expected == list(get_children("")):
            return
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

    def _update_toolbar_responsive(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        compact = self.root.winfo_width() < 1100
        if compact == self._toolbar_compact:
            return
        self._toolbar_compact = compact
        self.match_actions.grid_configure(
            row=1 if compact else 0,
            column=0 if compact else 1,
            columnspan=2 if compact else 1,
            sticky="w" if compact else "e",
            pady=(8, 0) if compact else 0,
        )
        for button in self.toolbar_buttons:
            button.set_padding(9 if compact else 12)
        self.update_action_labels()

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
            item.iid = self.tree.insert("", "end", values=("", ""))
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
        detected_mode = _detect_media_mode(paths, self.media_mode.get())
        if not detected_mode:
            return
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
                        files.extend(scanned[raw])
                    elif path.suffix.lower() in VIDEO_EXT:
                        files.append(path)
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
                it.iid = self.tree.insert("", "end", values=("", ""))
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
            self._search_new_names.pop(item.iid, None)
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
        selected = getattr(self, "_bulk_selection", None)
        if selected is None:
            selected = self.tree.selection()
        mark = "\u2611" if item.iid in selected else "\u2610"
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
                new = (
                    tv_target_name_display(target)
                    if item.media_type == "tv"
                    else target_name_display(target)
                )
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
            "display": new,
        }
        search_new_name = new
        item.status_detail = self._status_detail(item, new_path)
        font = tkfont.nametofont("TkTextFont")
        original = Path(item.orig_path).name
        original = self._middle_clip(
            original, font,
            max(
                1,
                int(self.tree.column("#0", "width"))
                - font.measure(f"{mark}  ") - 36,
            ),
        )
        new = self._middle_clip(
            new, font,
            max(1, int(self.tree.column("new", "width")) - 36),
        )
        self.tree.item(item.iid, tags=(),
                       text=f"{mark}  {original}",
                       values=(
                           "",
                           new,
                       ))
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
        iid = self.tree.identify_row(event.y)
        item = self.by_iid.get(iid)
        self.tree.configure(
            cursor=(
                "hand2"
                if item
                and item.status != "done"
                and self._is_manual_match_cell(iid, event.x)
                else ""
            ),
        )
        if iid == getattr(self, "_hover_iid", None):
            return
        self.clear_tree_hover()
        if iid:
            tags = self.tree.item(iid, "tags")
            self.tree.item(iid, tags=("hover", *tags))
            self._hover_iid = iid
        self._schedule_status_cells()

    def clear_tree_hover(self, _event=None):
        self.tree.configure(cursor="")
        iid = getattr(self, "_hover_iid", None)
        if iid and self.tree.exists(iid):
            tags = tuple(
                tag for tag in self.tree.item(iid, "tags")
                if tag != "hover"
            )
            self.tree.item(iid, tags=tags)
        self._hover_iid = None
        self._schedule_status_cells()

    @contextlib.contextmanager
    def _bulk_refresh(self):
        """Cache the tree selection while many rows are refreshed at once."""
        previous = getattr(self, "_bulk_selection", None)
        tree = getattr(self, "tree", None)
        self._bulk_selection = (
            set(tree.selection()) if tree is not None else None
        )
        try:
            yield
        finally:
            self._bulk_selection = previous

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
    def _auto_match_eligible(item, selected_source="automatic"):
        media_type = getattr(item, "media_type", "movie")
        match = getattr(item, "match", None) or {}
        match_id = match.get("id")
        selected_source_mismatch = (
            selected_source != "automatic"
            and item.status in ("auto", "no_change")
            and isinstance(match_id, int)
            and not isinstance(match_id, bool)
            and provider_of(match_id) != selected_source
        )
        return (
            getattr(item, "match_source", None) != "manual"
            and (
                item.status not in ("done", "auto", "manual", "no_change")
                or selected_source_mismatch
            )
            and item.error_code not in (
                "wrong_mode_movie_in_tv", "wrong_mode_tv_in_movies",
            )
            and (media_type != "tv" or getattr(item, "is_tv_episode", False))
        )

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
        items = list(items)
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
            )
        ]
        key = self.key_var.get().strip()
        self._configure_resolver()
        if not items:
            return
        if items:
            self._clear_review_filter()

        run = self._matching_run
        if run is None:
            self._match_run_id += 1
            run = {
                "id": self._match_run_id,
                "items": [],
                "originals": {},
                "history_before": {},
                "completed": set(),
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

    def match_run(self):
        """Match selected rows, or all eligible rows if nothing is selected."""
        chosen = self._action_scope_items()
        self.start_match(chosen, show_dialog=True)

    def rematch_selected(self):
        self.start_match(self.selected(), show_dialog=True)

    def worker(self):
        tmdb = None

        while True:
            job = self.jobs.get()
            if job is None:
                return
            run_id, cancel_event, job_items, key, lang, thr, operation_cache = job
            items = (
                job_items if isinstance(job_items, (list, tuple))
                else [job_items]
            )
            if cancel_event.is_set():
                continue
            for item in items:
                self.results.put(("started", run_id, item))
            try:
                if (
                    tmdb is None
                    or tmdb.key != key
                    or tmdb.language != lang
                    or tmdb.cancel_event is not cancel_event
                    or tmdb.operation_cache is not operation_cache
                    or tmdb.settings != self.provider_settings
                ):
                    tmdb = ProviderRouter(
                        self.provider_settings, key, lang,
                        cancel_event=cancel_event,
                        operation_cache=operation_cache,
                    )

                first_item = items[0]
                search_query = normalize_search_title(first_item.query)
                selected_source = self.provider_settings.selected
                selected_provider = (
                    tmdb._provider(selected_source)
                    if selected_source != "automatic" else None
                )
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
                        tmdb, self.show_resolver, items, operation_cache,
                        thr, self._review_reason,
                    )
                else:
                    results = match_movie_items(
                        tmdb, items, thr, search_query,
                    )
            except TMDBAuthError as error:
                cancel_event.set()
                self.results.put((
                    "auth_error", run_id, items[0], str(error),
                ))
                continue
            except TMDBCancelled:
                continue
            except Exception as e:
                logging.getLogger(__name__).exception(
                    "Matching worker failed.",
                )
                if sys.stderr is not None:
                    traceback.print_exc(file=sys.stderr)
                results = [
                    ("error", None, str(e))
                    for _item in items
                ]
            if not cancel_event.is_set():
                for item, out in zip(items, results):
                    self.results.put(("finished", run_id, item, *out))

    def poll(self):
        self._process_match_events()
        self.root.after(15 if not self.results.empty() else 100, self.poll)

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
                    run["current"] = None
                    pending["progress"] = True
                    if run["done"] >= run["total"]:
                        flush()
                        self._finish_match_run(run)
        except queue.Empty:
            pass
        flush()

    def _stop_matching_for_auth(self, run, message):
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
        self._matching_run = None
        self.job_total = self.job_done = 0
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
            self._match_progress_dialog.update_progress(
                filename, run["done"], run["total"],
                matched, flagged, failed,
            )
        elif run["total"]:
            suffix = (
                f" \u00b7 {run['manual_kept']} manual matches kept"
                if run["manual_kept"] else ""
            )
            self.status(
                f"Matching {run['done']}/{run['total']}...{suffix}",
            )

    def _finish_match_run(self, run):
        if self._matching_run is not run:
            return
        matched, flagged, failed = self._match_counts(run)
        if run["show_dialog"] and self._match_progress_dialog:
            self._match_progress_dialog.complete(
                run["done"], run["total"], matched, flagged, failed,
            )
        message = (
            f"Matching finished: {matched} ready, {flagged} to review, "
            f"{failed} failed."
        )
        if run["manual_kept"]:
            message += f" {run['manual_kept']} manual matches kept."
        self.status(message)
        self._record_match_history(run)
        self._discard_match_cache(run)
        self._matching_run = None
        self.job_total = self.job_done = 0
        self._update_undo_button()

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
        if self._match_progress_dialog:
            self._match_progress_dialog.complete(
                run["done"], run["total"], matched, flagged, failed,
                cancelled=True,
            )
        self.status(
            f"Matching canceled after {run['done']} of {run['total']} files.",
        )
        self._matching_run = None
        self.job_total = self.job_done = 0
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
        if region == "separator":
            return None
        if region == "heading":
            if self.tree.identify_column(event.x) == "#0":
                self.toggle_all()
                return "break"
            return None
        self.tree.focus_set()
        row = self.tree.identify_row(event.y)
        if not row:
            self.deselect()
            return "break"
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
        if region == "separator":
            self._auto_fit_column(self.tree.identify_column(event.x))
            return "break"
        if region not in ("cell", "tree"):
            return None
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
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
        sel = set(self.tree.selection())
        for it in self.items:
            mark = "\u2611" if it.iid in sel else "\u2610"
            font = tkfont.nametofont("TkTextFont")
            original = self._middle_clip(
                Path(it.orig_path).name,
                font,
                max(
                    1,
                    int(self.tree.column("#0", "width"))
                    - font.measure(f"{mark}  ") - 36,
                ),
            )
            self.tree.item(it.iid, text=f"{mark}  {original}")
        visible = set(self._visible_item_iids())
        selected_visible = visible.intersection(sel)
        mark = (
            "\u2611" if visible and len(selected_visible) == len(visible)
            else "\u25e9" if selected_visible else "\u2610"
        )
        self.tree.heading("#0", text=f"{mark}  Original name")
        self.schedule_summary()
        self._schedule_status_cells()

    def update_action_labels(self):
        selected_items = self.selected()
        selected = len(selected_items)
        total = len(getattr(self, "items", ()))
        subset_selected = 0 < selected < total
        self.match_btn_var.set(
            f"Auto Match ({selected})" if subset_selected else "Auto Match"
        )
        self.manual_match_btn_var.set(
            f"Manual Match ({selected})"
            if subset_selected else "Manual Match"
        )
        rename_count = sum(
            1 for item in selected_items
            if item.match
            and item.status in ("auto", "manual", "review")
        )
        self.apply_btn_var.set(
            f"Rename ({rename_count})" if subset_selected else "Rename All"
        )

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
                text = "No match yet for this file."
            else:
                m = it.match
                try:
                    dest = (
                        it.path if it.status == "done"
                        else self.plan_for(it).dest
                    )
                except ValueError as error:
                    text = str(error)
                else:
                    text = f"{m['title']} ({m['year'] or '????'})   \u2192   {dest}"
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
            try:
                self.column_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.column_menu.grab_release()
            return "break"
        if region == "separator":
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
        if sel[0].error_code in (
            "wrong_mode_movie_in_tv", "wrong_mode_tv_in_movies",
        ):
            self.status(sel[0].status_detail)
            return
        if sel[0].status == "done":
            show_info(self, "Already applied", "Use Undo to reverse the Rename.")
            return
        if sel[0].media_type == "tv" and not sel[0].is_tv_episode:
            show_info(
                self,
                "Episode information needed",
                "This TV filename needs a season and episode number before "
                "it can be matched or organized.",
            )
            return
        self._configure_resolver()
        self._start_manual_matches([sel[0]])

    def match_manually(self):
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
        ops, errors = [], []

        def apply_folder_renames(start):
            for operation in ops[start:]:
                if operation[0] != "rename_dir":
                    continue
                old_dir, new_dir = operation[1], operation[2]
                for other in self.items:
                    path = rebase(other.path, old_dir, new_dir)
                    root = rebase(other.root, old_dir, new_dir)
                    if path != other.path or root != other.root:
                        other.path, other.root = path, root

        for it in todo:
            plan = plans[id(it)]
            new_path, error = apply_item_plan(
                it, plan, ops, self.rename_subtitles.get(),
                apply_folder_renames,
            )
            if error is not None:
                e = error
                it.error_code = self._error_code_for_exception(e)
                it.error = self._safe_exception_message(e)
                it.status = "error"
                errors.append(f"{it.path.name}: {e}")
                self.refresh_row(it)
                continue
            it.path, it.status, it.error = new_path, "done", ""
            it.error_code = None
            pending_show = getattr(it, "_pending_manual_show", None)
            if pending_show:
                choice, details = pending_show
                try:
                    self.remember_show(it, choice, details)
                except (RuntimeError, ValueError) as error:
                    errors.append(
                        f"{it.path.name}: Could not remember TV show: {error}"
                    )
                else:
                    it._pending_manual_show = None
        self._vcount.clear()
        self._push_rename_undo(rename_before, ops, len(todo))
        self.refresh_all()
        msg = f"Done. {sum(1 for o in ops if o[0] == 'move')} file(s) moved/renamed."
        if errors:
            msg += "\n\nProblems:\n" + "\n".join(errors[:8])
        self.status("Applied.")
        self._rename_running = False
        self._update_undo_button()
        show_info(self, "Finished", msg)

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
