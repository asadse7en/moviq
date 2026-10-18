import io
import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from PIL import Image, ImageTk
import requests

from .. import __version__ as APP_VERSION
from ..core.matching import (
    rank, rank_tv, score, to_choice, to_tv_choice, tv_score, tv_year_of,
    year_of,
)
from ..core.naming import similarity
from ..core.show_resolver import ShowState
from ..core.tmdb import MAX_WORKERS, TMDBOperationCache
from .popups import apply_titlebar_theme, set_window_icon


IMAGE_BASE = "https://image.tmdb.org/t/p"
DETAIL_POSTER_SIZE = (88, 132)
ID_RE = re.compile(r"^(?:tmdb\s*:\s*)?(\d+)$", re.IGNORECASE)


def _match_dialog_sizes(screen_width, screen_height):
    width = min(1100, max(1, screen_width - 40))
    height = min(600, max(1, screen_height - 80))
    return width, height, min(1000, width), min(560, height)


class SearchDialog(tk.Toplevel):
    def __init__(self, app, item, on_close=None, operation_cache=None):
        super().__init__(app.root)
        set_window_icon(self)
        self.app = app
        self.item = item
        self.on_close = on_close
        self.is_tv = item.media_type == "tv"
        self.operation_cache = operation_cache or TMDBOperationCache()
        self._owns_operation_cache = operation_cache is None
        self.tmdb = app.tmdb(operation_cache=self.operation_cache)
        self.results = []
        self.selected_index = None
        self.selected_result = None
        self.selected_details = None
        self._result_widgets = []
        self._poster_data = {}
        self._poster_requests = set()
        self._result_queue = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._cancel_event = threading.Event()
        if self.tmdb is not None:
            self.tmdb.cancel_event = self._cancel_event
        self._closed = False
        self._search_after_id = None
        self._search_generation = 0
        self._requested_details_id = None
        self._requested_episode_id = None
        self._details_poster_path = None
        self._direct_id_search = False
        self._poll_after_id = None

        if self.tmdb is None:
            self._close()
            return

        self.title("Edit Match")
        width, height, min_width, min_height = _match_dialog_sizes(
            self.winfo_screenwidth(), self.winfo_screenheight(),
        )
        self.geometry(f"{width}x{height}")
        self.minsize(min_width, min_height)
        self.transient(app.root)
        self.configure(bg=app.pal["card"])
        self.protocol("WM_DELETE_WINDOW", self.cancel)

        self._configure_styles()
        self._build_ui()
        self.update_idletasks()
        self._center()
        apply_titlebar_theme(self, self.app.mode == "dark")
        self.grab_set()
        self.search_entry.focus_set()
        self.bind("<Escape>", lambda _event: self.cancel())

        self._display_current_match()
        self.query_var.trace_add("write", self._on_query_changed)
        initial_query = item.match.get("title") if item.match else item.query
        initial_year = (item.match or {}).get("year") or item.year
        if initial_year and not self._split_query(initial_query)[1]:
            initial_query = f"{initial_query} {initial_year}"
        self.query_var.set(initial_query)
        self._update_placeholder()
        self._poll_results()
        self._schedule_search(delay=100)

    def _configure_styles(self):
        palette = self.app.pal
        style = ttk.Style(self)
        style.configure(
            "EditPanel.TFrame",
            background=palette["card"],
        )
        style.configure(
            "EditTitle.TLabel",
            background=palette["card"],
            foreground=palette["fg"],
            font="TkHeadingFont",
        )
        style.configure(
            "EditMuted.TLabel",
            background=palette["card"],
            foreground=palette["muted"],
            font="TkTextFont",
        )
        style.configure(
            "EditSection.TLabel",
            background=palette["card"],
            foreground=palette["accent"],
            font="TkHeadingFont",
        )
        style.configure(
            "EditBadge.TLabel",
            background=palette["card"],
            foreground=palette["accent"],
            font="TkHeadingFont",
            padding=(0, 0),
        )
    def _build_ui(self):
        palette = self.app.pal
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, style="EditPanel.TFrame", padding=(14, 10, 14, 6))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text="Edit TV show match" if self.is_tv else "Edit movie match",
            style="EditTitle.TLabel",
            font="TkHeadingFont",
        ).grid(row=0, column=0, sticky="w")
        self.file_var = tk.StringVar()
        ttk.Label(
            header,
            textvariable=self.file_var,
            style="EditMuted.TLabel",
            wraplength=680,
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.current_match_var = tk.StringVar()
        ttk.Label(
            header,
            textvariable=self.current_match_var,
            style="EditMuted.TLabel",
            wraplength=680,
        ).grid(row=2, column=0, sticky="w", pady=(3, 0))

        body = tk.Frame(
            self,
            bg=palette["card"],
        )
        body.grid(
            row=1, column=0, sticky="nsew", padx=14, pady=(0, 6),
        )
        body.columnconfigure(0, weight=52, uniform="match-panels")
        body.columnconfigure(1, minsize=1)
        body.columnconfigure(2, weight=48, uniform="match-panels")
        body.rowconfigure(0, weight=1)

        self.results_panel = tk.Frame(
            body,
            bg=palette["card"],
        )
        self.results_panel.grid(
            row=0, column=0, sticky="nsew", padx=(10, 8), pady=5,
        )
        self.results_panel.columnconfigure(0, weight=1)
        self.results_panel.rowconfigure(2, weight=1)
        results_heading = ttk.Frame(
            self.results_panel,
            style="EditPanel.TFrame",
            padding=(10, 8, 10, 6),
        )
        results_heading.grid(row=0, column=0, sticky="ew")
        results_heading.columnconfigure(0, weight=1)
        ttk.Label(
            results_heading, text="SEARCH RESULTS", style="EditSection.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.result_count_var = tk.StringVar(value="")
        ttk.Label(
            results_heading,
            textvariable=self.result_count_var,
            style="EditMuted.TLabel",
        ).grid(row=0, column=1, sticky="e")

        search_frame = tk.Frame(self.results_panel, bg=palette["card"])
        search_frame.grid(row=1, column=0, sticky="ew", padx=9, pady=(0, 7))
        search_box = tk.Frame(
            search_frame,
            bg=palette["card"],
            highlightthickness=1,
            highlightbackground=palette.get("mode_border", palette["btn"]),
            highlightcolor=palette["accent"],
        )
        search_box.pack(fill="x")
        self.query_var = tk.StringVar()
        search_input = tk.Frame(search_box, bg=palette["card"])
        search_input.pack(side="left", fill="both", expand=True)
        self.search_entry = tk.Entry(
            search_input,
            textvariable=self.query_var,
            bg=palette["card"],
            fg=palette["fg"],
            insertbackground=palette["fg"],
            selectbackground=palette.get("selection", palette["accent"]),
            selectforeground=palette["fg"],
            relief="flat",
            bd=0,
            highlightthickness=0,
            font="TkTextFont",
        )
        self.search_entry.pack(fill="both", expand=True, padx=(10, 4), pady=7)
        self.placeholder_label = tk.Label(
            search_input,
            text="Search TMDB ID, movie, show, or season/episode...",
            bg=palette["card"],
            fg=palette["muted"],
            font="TkTextFont",
            anchor="w",
            padx=11,
        )
        self.placeholder_label.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.placeholder_label.bind(
            "<Button-1>", lambda _event: self.search_entry.focus_set(),
        )
        self.search_icon = tk.Canvas(
            search_box,
            width=18,
            height=18,
            bg=palette["card"],
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            takefocus=True,
        )
        self.search_icon.pack(side="right", padx=(4, 11), pady=7)
        self.search_icon.create_oval(
            2, 2, 10, 10,
            outline=palette["muted"],
            width=1.6,
            tags=("icon",),
        )
        self.search_icon.create_line(
            8, 8, 15, 15,
            fill=palette["muted"],
            width=1.6,
            capstyle="round",
            tags=("icon",),
        )
        self.search_icon.bind("<Button-1>", self._submit_search)
        self.search_icon.bind("<Return>", self._submit_search)
        self.search_entry.bind("<Return>", self._submit_search)
        self.search_entry.bind("<Down>", self._focus_first_result)
        self.search_entry.bind("<Tab>", self._tab_from_search)
        self.search_entry.bind("<Shift-Tab>", self._tab_back_from_search)

        result_area = tk.Frame(self.results_panel, bg=palette["card"])
        result_area.grid(row=2, column=0, sticky="nsew", padx=(9, 0))
        result_area.columnconfigure(0, weight=1)
        result_area.rowconfigure(0, weight=1)
        self.results_canvas = tk.Canvas(
            result_area,
            bg=palette["card"],
            bd=0,
            highlightthickness=0,
        )
        self.results_canvas.grid(row=0, column=0, sticky="nsew")
        self.results_list = tk.Frame(self.results_canvas, bg=palette["card"])
        self.results_cards = tk.Frame(self.results_list, bg=palette["card"])
        self.results_cards.grid(
            row=0, column=0, sticky="new", padx=5, pady=(8, 0),
        )
        self.results_list.columnconfigure(0, weight=1)
        self._results_window = self.results_canvas.create_window(
            (0, 0), window=self.results_list, anchor="nw",
        )
        self.results_list.bind(
            "<Configure>", self._update_results_scrollregion,
        )
        self.results_cards.bind(
            "<Configure>", self._fit_results_window,
        )
        self.results_canvas.bind(
            "<Configure>", self._resize_results_list,
        )
        self._bind_results_mousewheel(self.results_canvas)
        self.search_status_var = tk.StringVar(value="Preparing search...")
        ttk.Label(
            self.results_panel,
            textvariable=self.search_status_var,
            style="EditMuted.TLabel",
            padding=(10, 6, 10, 6),
        ).grid(row=3, column=0, sticky="ew")

        self.details_panel = tk.Frame(body, bg=palette["card"])
        self.details_panel.grid(
            row=0, column=2, sticky="nsew", padx=(8, 10), pady=5,
        )
        self.details_panel.columnconfigure(0, weight=1)
        self.details_panel.rowconfigure(1, weight=1)
        ttk.Label(
            self.details_panel,
            text="EXPANDED DETAILS",
            style="EditSection.TLabel",
            padding=(10, 7, 10, 5),
        ).grid(row=0, column=0, sticky="w")

        self.details_content = tk.Frame(
            self.details_panel, bg=palette["card"], padx=10, pady=2,
        )
        self.details_content.grid(row=1, column=0, sticky="nsew")
        self.details_content.columnconfigure(1, weight=1)
        self.details_poster = tk.Label(
            self.details_content,
            text="No\nposter",
            bg=palette["card"],
            fg=palette["muted"],
            width=11,
            height=7,
            font="TkTextFont",
        )
        self.details_poster.grid(
            row=0, column=0, rowspan=5, sticky="nw", padx=(0, 9),
        )
        self.details_title_var = tk.StringVar(value="Select a result")
        self.details_title_label = ttk.Label(
            self.details_content,
            textvariable=self.details_title_var,
            style="EditTitle.TLabel",
            wraplength=250,
        )
        self.details_title_label.grid(
            row=0, column=1, sticky="nw", pady=(0, 3),
        )
        self.details_id_var = tk.StringVar(value="")
        metadata_row = tk.Frame(self.details_content, bg=palette["card"])
        metadata_row.grid(row=1, column=1, sticky="ew")
        metadata_row.columnconfigure(0, weight=1)
        self.details_id_label = ttk.Label(
            metadata_row,
            textvariable=self.details_id_var,
            style="EditMuted.TLabel",
        )
        self.details_id_label.grid(row=0, column=0, sticky="w")
        self.confidence_var = tk.StringVar(value="")
        self.confidence_label = ttk.Label(
            metadata_row,
            textvariable=self.confidence_var,
            style="EditBadge.TLabel",
        )
        self.confidence_label.grid(row=0, column=1, sticky="e")
        self.genres_var = tk.StringVar(value="")
        self.genres_label = ttk.Label(
            self.details_content,
            textvariable=self.genres_var,
            style="EditMuted.TLabel",
            wraplength=250,
        )
        self.genres_label.grid(
            row=2, column=1, sticky="nw", pady=(3, 0),
        )

        self.director_var = tk.StringVar(value="")
        self.director_label = ttk.Label(
            self.details_content,
            textvariable=self.director_var,
            style="EditMuted.TLabel",
            wraplength=250,
        )
        self.director_label.grid(
            row=3, column=1, sticky="nw", pady=(2, 0),
        )

        self.cast_var = tk.StringVar(value="")
        self.cast_label = ttk.Label(
            self.details_content,
            textvariable=self.cast_var,
            style="EditMuted.TLabel",
            wraplength=250,
        )
        self.cast_label.grid(
            row=4, column=1, sticky="nw", pady=(2, 0),
        )
        self._details_font = tkfont.nametofont("TkTextFont")
        self._genre_text = ""
        self._director_text = ""
        self._cast_names = []
        self._overview_text = ""

        self.overview_var = tk.StringVar(value="")
        ttk.Label(
            self.details_content,
            text="OVERVIEW",
            style="EditSection.TLabel",
            padding=(0, 4, 0, 1),
        ).grid(row=5, column=0, columnspan=2, sticky="w")
        self.overview_label = ttk.Label(
            self.details_content,
            textvariable=self.overview_var,
            style="EditMuted.TLabel",
            wraplength=330,
            justify="left",
        )
        self.overview_label.grid(
            row=6, column=0, columnspan=2, sticky="new",
        )

        self.tv_section_title = ttk.Label(
            self.details_content,
            text="SELECTED EPISODE",
            style="EditSection.TLabel",
            padding=(0, 4, 0, 1),
        )
        self.tv_section_title.grid(
            row=7, column=0, columnspan=2, sticky="w",
        )
        self.tv_info_var = tk.StringVar(value="")
        self.tv_info_label = ttk.Label(
            self.details_content,
            textvariable=self.tv_info_var,
            style="EditMuted.TLabel",
            wraplength=330,
            justify="left",
        )
        self.tv_info_label.grid(
            row=8, column=0, columnspan=2, sticky="new",
        )
        if not self.is_tv:
            self.tv_section_title.grid_remove()
            self.tv_info_label.grid_remove()
        self.details_panel.bind(
            "<Configure>",
            lambda _event: self.after_idle(self._resize_details_text),
            add="+",
        )
        self.details_content.bind(
            "<Configure>",
            lambda _event: self.after_idle(self._resize_details_text),
            add="+",
        )
        tk.Frame(
            body, bg=palette.get("mode_border", palette["btn"]), width=1,
        ).grid(row=0, column=1, sticky="ns", pady=6)

        footer = tk.Frame(self, bg=palette["card"], padx=14, pady=7)
        footer.grid(row=3, column=0, sticky="ew")
        for column in range(3):
            footer.columnconfigure(column, weight=1)
        self.cancel_button = self._action_button(
            footer, "Cancel", self.cancel, "secondary",
        )
        self.cancel_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.skip_button = self._action_button(
            footer, "Skip", self.keep_current, "neutral",
        )
        self.skip_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.update_button = self._action_button(
            footer, "Match", self.update_match, "primary",
        )
        self.update_button.configure(state="disabled")
        self.update_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self.cancel_button.bind(
            "<Tab>", lambda _event: self._focus_widget(self.skip_button),
        )
        self.cancel_button.bind(
            "<Shift-Tab>", self._tab_back_from_action,
        )
        self.skip_button.bind(
            "<Shift-Tab>", lambda _event: self._focus_widget(self.cancel_button),
        )
        self.skip_button.bind(
            "<Tab>", lambda _event: self._focus_widget(self.update_button),
        )
        self.update_button.bind(
            "<Tab>", lambda _event: self._focus_widget(self.search_entry),
        )
        self.update_button.bind(
            "<Shift-Tab>", lambda _event: self._focus_widget(self.skip_button),
        )

    def _action_button(self, parent, text, command, role):
        palette = self.app.pal
        is_primary = role == "primary"
        background = palette["accent"] if is_primary else palette["btn"]
        foreground = "#ffffff" if is_primary else palette["muted"]
        hover_background = (
            palette["accent_hi"] if is_primary else palette["btn_hi"]
        )
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=background,
            fg=foreground,
            activebackground=hover_background,
            activeforeground=foreground,
            disabledforeground=foreground,
            relief="flat",
            overrelief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=16,
            pady=8,
            font="TkHeadingFont" if is_primary else "TkDefaultFont",
            cursor="hand2",
            takefocus=True,
        )
        button.bind(
            "<Enter>",
            lambda _event: button.configure(
                bg=hover_background,
            ) if str(button.cget("state")) != "disabled" else None,
        )
        button.bind(
            "<Leave>",
            lambda _event: button.configure(bg=background),
        )
        return button

    def _center(self):
        self.app.root.update_idletasks()
        self.update_idletasks()
        parent = self.app.root
        x = parent.winfo_rootx() + (
            parent.winfo_width() - self.winfo_width()
        ) // 2
        y = parent.winfo_rooty() + (
            parent.winfo_height() - self.winfo_height()
        ) // 2
        frame_x = self.winfo_rootx() - self.winfo_x()
        frame_y = self.winfo_rooty() - self.winfo_y()
        self.geometry(f"+{max(0, x - frame_x)}+{max(0, y - frame_y)}")

    def _display_current_match(self):
        match = self.item.match
        self.file_var.set(f"FILE: {self.item.path.name}")
        self.current_match_var.set("")
        if not match:
            return
        similarity = round(self._score(match, self.item.query) * 100)
        title = match.get("title", "Unknown title")
        year = match.get("year")
        year_text = f" ({year})" if year else ""
        chosen_manually = (
            getattr(self.item, "match_source", None) == "manual"
        )
        prefix = "Chosen manually" if chosen_manually else "Current match"
        confidence = "" if chosen_manually else f" · {similarity}%"
        self.current_match_var.set(
            f"{prefix}: {title}{year_text}{confidence}",
        )

    def _score(self, result, query):
        return tv_score(result, query) if self.is_tv else score(result, query)

    def _year(self, result):
        return tv_year_of(result) if self.is_tv else year_of(result)

    @staticmethod
    def _title(result, is_tv=False):
        if is_tv:
            return result.get("name") or result.get("original_name") or "Unknown"
        return result.get("title") or result.get("original_title") or "Unknown"

    def _result_title(self, result):
        title = self._title(result, self.is_tv)
        if self.is_tv and result.get("_matched_episode_query"):
            title += (
                f"  ·  S{self.item.season_number:02d}"
                f"E{self.item.episode_number:02d}"
                f"  ·  {result['episode_title']}"
            )
        year = self._year(result) or result.get("year")
        if year:
            title += f" ({year})"
        return title

    @staticmethod
    def _split_query(query):
        year_match = re.search(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)", query)
        if not year_match:
            return query.strip(), None
        title = (
            query[:year_match.start()] + " " + query[year_match.end():]
        ).strip(" \t-–—,.:()[]{}")
        if not title:
            return query.strip(), None
        return title, int(year_match.group())

    @staticmethod
    def _parse_tmdb_id(query):
        match = ID_RE.fullmatch(query.strip())
        return int(match.group(1)) if match else None

    def _update_placeholder(self):
        if self.query_var.get():
            self.placeholder_label.place_forget()
        else:
            self.placeholder_label.place(
                relx=0, rely=0, relwidth=1, relheight=1,
            )

    def _on_query_changed(self, *_args):
        self._update_placeholder()
        self._schedule_search()

    def _schedule_search(self, delay=350):
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
        self._search_generation += 1
        self._direct_id_search = False
        self.selected_index = None
        self.selected_result = None
        self.selected_details = None
        self._requested_details_id = None
        self._requested_episode_id = None
        self._details_poster_path = None
        self.results = []
        self._clear_result_cards()
        self.result_count_var.set("")
        self.update_button.configure(state="disabled")
        self._clear_details()
        query = self.query_var.get().strip()
        if not query:
            self.search_status_var.set("Enter a title or TMDB ID to search.")
            self._search_after_id = None
            return
        self.search_status_var.set("Waiting for more typing…")
        self._search_after_id = self.after(
            delay, self._start_search, self._search_generation,
        )

    def _submit_search(self, _event=None):
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
            self._search_after_id = None
        self._start_search(self._search_generation)
        return "break"

    def _start_search(self, generation):
        self._search_after_id = None
        query = self.query_var.get().strip()
        if not query:
            self.search_status_var.set("Enter a title or TMDB ID to search.")
            return
        tmdb_id = self._parse_tmdb_id(query)
        if tmdb_id is not None:
            self._direct_id_search = True
            self.search_status_var.set(f"Looking up TMDb ID {tmdb_id}…")
            self._executor.submit(
                self._load_by_id, generation, tmdb_id, self.is_tv,
            )
            return
        self._direct_id_search = False
        title, year = self._split_query(query)
        if not title:
            self.search_status_var.set("Type a title to search.")
            return
        self.search_status_var.set("Searching TMDb…")
        self._executor.submit(
            self._load_search, generation, title, year, self.is_tv,
        )

    def _load_search(self, generation, title, year, is_tv):
        try:
            if is_tv:
                resolved = self.app.resolve_show(
                    title, year, self.operation_cache,
                )
                if resolved.state == ShowState.ERROR:
                    raise RuntimeError(
                        resolved.error or "TV show lookup failed.",
                    )
                if resolved.state == ShowState.RESOLVED:
                    result = dict(resolved.candidate or {})
                    result.update({
                        "id": resolved.tmdb_id,
                        "name": resolved.name,
                        "year": resolved.year,
                    })
                    results = [result]
                else:
                    results = []
            else:
                results = self.tmdb.search(title, year)
            if (
                is_tv and not results
                and self.item.season_number is not None
                and self.item.episode_number is not None
            ):
                results = self._search_current_episode_title(title)
            error = None
        except Exception as exception:
            results = []
            error = str(exception)
        self._result_queue.put((
            "search", generation, title, results, error,
        ))

    def _search_current_episode_title(self, query):
        resolved = self.app.resolve_show(
            self.item.query, self.item.year, self.operation_cache,
        )
        if resolved.state != ShowState.RESOLVED:
            return []
        show = dict(
            resolved.candidate or self.tmdb.tv(resolved.tmdb_id) or {},
        )
        show.update({
            "id": resolved.tmdb_id,
            "name": resolved.name,
            "year": resolved.year,
        })
        episode = self._season_episode(show["id"])
        episode_title = (episode or {}).get("name")
        if (
            not episode_title
            or similarity(query, episode_title) < 0.8
        ):
            return []
        result = dict(show)
        result["episode_title"] = episode_title
        result["_matched_episode_query"] = True
        return [result]

    def _season_episode(self, show_id):
        season = self.tmdb.season(show_id, self.item.season_number) or {}
        for episode in season.get("episodes") or []:
            if episode.get("episode_number") == self.item.episode_number:
                episode = dict(episode)
                episode.setdefault("season_number", self.item.season_number)
                return episode
        return None

    def _episode_details_for_show(self, show_id):
        numbers = self.item.episode_numbers or (self.item.episode_number,)
        season_number = self.item.season_number
        use_absolute = not self.item.season_was_explicit
        if not use_absolute:
            season = self.tmdb.season(show_id, season_number) or {}
            use_absolute = (
                self.item.episode_number
                > len(season.get("episodes") or [])
            )
        if use_absolute:
            episodes = [
                self.tmdb.absolute_episode(show_id, number)
                for number in numbers
            ]
            if (
                not all(episodes)
                or len({
                    episode.get("season_number") for episode in episodes
                }) != 1
                or any(
                    not isinstance(episode.get("episode_number"), int)
                    for episode in episodes
                )
                or any(
                    episodes[index]["episode_number"]
                    != episodes[0]["episode_number"] + index
                    for index in range(len(episodes))
                )
            ):
                return None, (
                    f"Absolute episode number {self.item.episode_number} "
                    "couldn't be mapped."
                ), None
            season_number = episodes[0]["season_number"]
            episode_numbers = [
                episode["episode_number"] for episode in episodes
            ]
        else:
            episode_numbers = list(numbers)
            season = self.tmdb.season(show_id, season_number) or {}
            by_number = {
                episode.get("episode_number"): episode
                for episode in season.get("episodes") or []
            }
            episodes = [by_number.get(number) for number in episode_numbers]
            if not all(episodes):
                return None, (
                    "TMDb episode information is unavailable for "
                    f"S{season_number:02d}E{self.item.episode_number:02d}."
                ), None
        titles = [episode.get("name") for episode in episodes]
        if not all(titles):
            return None, "TMDb episode titles are unavailable.", None
        title = " & ".join(titles)
        end_number = (
            episode_numbers[-1] if len(episode_numbers) > 1 else None
        )
        return title, None, (season_number, episode_numbers[0], end_number)

    def _load_by_id(self, generation, tmdb_id, is_tv):
        try:
            details = (
                self.tmdb.tv(tmdb_id) if is_tv
                else self.tmdb.movie(tmdb_id)
            )
            error = None
        except Exception as exception:
            details = None
            error = str(exception)
        self._result_queue.put((
            "id", generation, tmdb_id, details, error,
        ))
        if details and is_tv:
            self._executor.submit(
                self._load_episode, generation, tmdb_id,
            )

    def _show_search_results(self, generation, query, results, error):
        if generation != self._search_generation:
            return
        if error:
            self.search_status_var.set(f"Search failed: {error}")
            return
        self.results = (
            rank_tv(results, query) if self.is_tv else rank(results, query)
        )[:30]
        self._populate_result_cards()
        if not self.results:
            self.result_count_var.set("0")
            label = "TV shows" if self.is_tv else "movies"
            self.search_status_var.set(f"No matching {label} found.")
            self._clear_details()
            return
        self.result_count_var.set(str(len(self.results)))
        self.search_status_var.set(
            f"{len(self.results)} possible match(es).",
        )
        current_id = (self.item.match or {}).get("id")
        selected_index = next(
            (
                index for index, result in enumerate(self.results)
                if current_id is not None
                and str(result.get("id")) == str(current_id)
            ),
            0,
        )
        self._select_result(selected_index, focus=False)

    def _show_id_result(self, generation, tmdb_id, details, error):
        if generation != self._search_generation:
            return
        if error:
            self.search_status_var.set(f"TMDb lookup failed: {error}")
            return
        if not details:
            self.search_status_var.set(
                f"No {'TV show' if self.is_tv else 'movie'} found with "
                f"TMDb ID {tmdb_id}.",
            )
            self.result_count_var.set("0")
            self._clear_details()
            return
        self.results = [details]
        self._direct_id_search = True
        self.result_count_var.set("1")
        self.search_status_var.set(f"Loaded TMDb ID {tmdb_id}.")
        self._populate_result_cards()
        self.selected_index = 0
        self.selected_result = details
        self.selected_details = details
        self._requested_details_id = tmdb_id
        self._set_card_selection()
        self._show_selected_details(details)
        if self.is_tv:
            self.search_status_var.set(
                "Loading episode details…",
            )

    def _populate_result_cards(self):
        self._clear_result_cards()
        palette = self.app.pal
        for index, result in enumerate(self.results):
            card = tk.Frame(
                self.results_cards,
                bg=palette["card"],
                padx=5,
                pady=0,
                takefocus=True,
                highlightthickness=1,
                highlightbackground=palette["card"],
            )
            card.pack(fill="x", padx=(0, 5), pady=(0, 1))
            card.rowconfigure(0, weight=1)
            text_column = tk.Frame(card, bg=palette["card"])
            text_column.grid(row=0, column=0, sticky="nsew")
            text_column.columnconfigure(0, weight=1)
            text_column.rowconfigure(0, weight=1)
            card.columnconfigure(0, weight=1)
            title = tk.Label(
                text_column,
                text=self._result_title(result),
                bg=palette["card"],
                fg=palette["fg"],
                font="TkHeadingFont",
                anchor="w",
                justify="left",
                wraplength=650,
            )
            title.grid(row=0, column=0, sticky="w")
            self._result_widgets.append((card, title))
            for widget in (card, text_column, title):
                widget.bind(
                    "<Button-1>",
                    lambda _event, selected=index: self._select_result(
                        selected,
                    ),
                )
                widget.bind(
                    "<Double-Button-1>",
                    lambda _event, selected=index: self._double_click_result(
                        selected,
                    ),
                )
                widget.bind(
                    "<Up>",
                    lambda _event, selected=index: self._move_selection(
                        selected, -1,
                    ),
                )
                widget.bind(
                    "<Down>",
                    lambda _event, selected=index: self._move_selection(
                        selected, 1,
                    ),
                )
                widget.bind(
                    "<Return>",
                    lambda _event: self._confirm_selected(),
                )
                widget.bind("<Tab>", self._tab_from_results)
                widget.bind("<Shift-Tab>", self._tab_back_from_results)
                widget.bind(
                    "<space>",
                    lambda _event, selected=index: self._select_result(
                        selected,
                    ),
                )
            self._bind_results_mousewheel(card)
        self.results_cards.update_idletasks()
        self._fit_results_window()
        self._update_results_scrollregion()

    def _clear_result_cards(self):
        for child in self.results_cards.winfo_children():
            child.destroy()
        self._result_widgets = []
        self.results_canvas.yview_moveto(0)
        self._fit_results_window()

    def _set_card_selection(self):
        palette = self.app.pal
        selected_bg = palette.get("selection", palette["accent"])
        for index, widgets in enumerate(self._result_widgets):
            card, title = widgets
            is_selected = index == self.selected_index
            background = selected_bg if is_selected else palette["card"]
            foreground = (
                palette["fg"] if palette.get("selection") else "#ffffff"
            ) if is_selected else palette["fg"]
            card.configure(
                bg=background,
                highlightbackground=(
                    palette["accent"] if is_selected else palette["card"]
                ),
            )
            title.configure(bg=background, fg=foreground)
            children = widgets[0].winfo_children()
            children[0].configure(bg=background)

    def _select_result(self, index, focus=True, details=None):
        if not 0 <= index < len(self.results):
            return
        self.selected_index = index
        self.selected_result = self.results[index]
        self.selected_details = dict(details or self.selected_result)
        if self.is_tv and not self.selected_details.get("episode_title"):
            episode_title = self._known_episode_title(self.selected_result)
            if episode_title:
                self.selected_details["episode_title"] = episode_title
        self._set_card_selection()
        self._show_selected_details(self.selected_details)
        if focus:
            if index < len(self._result_widgets):
                self._result_widgets[index][0].focus_set()
                self._show_card(index)
        if details is not None:
            self._requested_details_id = self.selected_result.get("id")
            return
        movie_id = self.selected_result.get("id")
        if (
            self.is_tv
            and movie_id
            and not self.selected_details.get("episode_title")
            and self._requested_episode_id != movie_id
        ):
            self._requested_episode_id = movie_id
            self._executor.submit(
                self._load_episode, self._search_generation, movie_id,
            )
        if self._requested_details_id == movie_id:
            return
        self._requested_details_id = movie_id
        self._request_details(self.selected_result)

    def _known_episode_title(self, result):
        match = self.item.match or {}
        if (
            match.get("id") == result.get("id")
            and match.get("season_number") == self.item.season_number
            and match.get("episode_number") == self.item.episode_number
        ):
            return match.get("episode_title")
        return None

    def _show_card(self, index):
        if not 0 <= index < len(self._result_widgets):
            return
        card = self._result_widgets[index][0]
        self.results_canvas.update_idletasks()
        card_top = (
            card.winfo_rooty() - self.results_canvas.winfo_rooty()
            + self.results_canvas.canvasy(0)
        )
        card_bottom = card_top + card.winfo_height()
        view_top = self.results_canvas.canvasy(0)
        view_bottom = view_top + self.results_canvas.winfo_height()
        content_height = max(1, self.results_list.winfo_height())
        if card_top < view_top:
            self.results_canvas.yview_moveto(card_top / content_height)
        elif card_bottom > view_bottom:
            self.results_canvas.yview_moveto(
                max(0, (card_bottom - self.results_canvas.winfo_height()))
                / content_height,
            )

    def _double_click_result(self, index):
        self._select_result(index)
        self._confirm_selected()
        return "break"

    def _move_selection(self, index, amount):
        if not self.results:
            return "break"
        next_index = min(max(index + amount, 0), len(self.results) - 1)
        self._select_result(next_index)
        return "break"

    def _focus_first_result(self, _event=None):
        if self.results:
            self._select_result(0)
        else:
            self.cancel_button.focus_set()
        return "break"

    def _focus_widget(self, widget):
        widget.focus_set()
        return "break"

    def _tab_from_search(self, _event=None):
        if self.results:
            index = (
                self.selected_index if self.selected_index is not None else 0
            )
            self._select_result(index)
        else:
            self.cancel_button.focus_set()
        return "break"

    def _tab_back_from_search(self, _event=None):
        self.update_button.focus_set()
        return "break"

    def _tab_from_results(self, _event=None):
        self.cancel_button.focus_set()
        return "break"

    def _tab_back_from_results(self, _event=None):
        self.search_entry.focus_set()
        return "break"

    def _tab_back_from_action(self, _event=None):
        if self.results:
            index = (
                self.selected_index if self.selected_index is not None else 0
            )
            self._select_result(index)
        else:
            self.search_entry.focus_set()
        return "break"

    def _confirm_selected(self):
        if str(self.update_button.cget("state")) != "disabled":
            self.update_match()
        return "break"

    def _request_details(self, result):
        self._executor.submit(
            self._load_details, self._search_generation, result,
        )

    def _load_details(self, generation, result):
        tmdb_id = result.get("id")
        details = None
        error = None
        media = dict(result)
        try:
            if self.is_tv:
                details = self.tmdb.tv(tmdb_id) if tmdb_id else None
            else:
                details = self.tmdb.movie(tmdb_id) if tmdb_id else None
            if details is None:
                error = "TMDb did not return details."
            if details:
                media.update(details)
        except Exception as exception:
            error = str(exception)
        self._result_queue.put((
            "details",
            generation,
            tmdb_id,
            media,
            error,
        ))

    def _load_episode(self, generation, tmdb_id):
        try:
            episode_title, error, coordinates = (
                self._episode_details_for_show(tmdb_id)
            )
        except Exception as exception:
            episode_title = None
            error = str(exception)
            coordinates = None
        self._result_queue.put((
            "episode", generation, tmdb_id, episode_title, error,
            coordinates,
        ))

    def _request_details_poster(self, result_index, poster_path):
        if not poster_path or poster_path in self._poster_data:
            self._apply_cached_poster(result_index, poster_path)
            return
        request_key = (self._search_generation, poster_path)
        if request_key in self._poster_requests:
            return
        self._poster_requests.add(request_key)
        self._executor.submit(
            self._load_poster,
            self._search_generation,
            result_index,
            poster_path,
        )

    def _load_poster(self, generation, result_index, poster_path):
        try:
            response = requests.get(
                f"{IMAGE_BASE}/w185{poster_path}",
                headers={"User-Agent": f"Moviq/{APP_VERSION}"},
                timeout=12,
            )
            response.raise_for_status()
            image_data = response.content
            error = None
        except Exception as exception:
            image_data = None
            error = str(exception)
        self._result_queue.put((
            "poster",
            generation,
            result_index,
            poster_path,
            image_data,
            error,
        ))

    def _apply_cached_poster(self, result_index, poster_path):
        if not poster_path or not 0 <= result_index < len(self.results):
            return
        data = self._poster_data.get(poster_path)
        if data is None:
            return
        result = self.results[result_index]
        try:
            if (
                self.selected_result is result
                and self._details_poster_path == poster_path
            ):
                image = self._cache_image(data, DETAIL_POSTER_SIZE)
                self.details_poster.configure(
                    image=image, text="", width=0, height=0,
                )
                self.details_poster.image = image
        except (OSError, ValueError) as exception:
            self._poster_data.pop(poster_path, None)
            self._show_poster_unavailable(
                result_index, poster_path, str(exception),
            )

    def _load_poster_result(
        self, generation, result_index, poster_path, image_data, error,
    ):
        self._poster_requests.discard((generation, poster_path))
        if generation != self._search_generation:
            return
        if error or not image_data:
            self._show_poster_unavailable(
                result_index,
                poster_path,
                error or "TMDb returned an empty image.",
            )
            return
        self._poster_data[poster_path] = image_data
        self._apply_cached_poster(result_index, poster_path)

    def _show_poster_unavailable(self, result_index, poster_path, error):
        if not 0 <= result_index < len(self.results):
            return
        result = self.results[result_index]
        if (
            self.selected_result is result
            and self._details_poster_path == poster_path
        ):
            self.details_poster.configure(
                image="",
                text="Poster\nunavailable",
                width=11,
                height=7,
            )
            self.details_poster.image = None
            self.search_status_var.set(f"Poster unavailable: {error}")

    def _show_selected_details(self, details):
        result = details or {}
        title = self._title(result, self.is_tv)
        year = self._year(result) or result.get("year")
        self.details_title_var.set(
            f"{title}{f' ({year})' if year else ''}",
        )
        self.details_id_var.set(f"TMDB ID: {result.get('id', '—')}")
        similarity = round(self._score(result, self.item.query) * 100)
        self.confidence_var.set(f"{similarity}% CONFIDENCE")
        genres = result.get("genres") or []
        if genres and isinstance(genres[0], dict):
            genres = [genre.get("name", "") for genre in genres]
        genre_text = ", ".join(str(name) for name in genres if name)
        self._genre_text = f"Genres: {genre_text or '—'}"
        if self.is_tv:
            creators = result.get("created_by") or []
            creator = ", ".join(
                person.get("name", "") for person in creators
                if person.get("name")
            )
            self._director_text = f"Creator: {creator or '—'}"
            credits = result.get("credits") or {}
        else:
            credits = result.get("credits") or {}
            directors = [
                person.get("name", "")
                for person in credits.get("crew", [])
                if person.get("job") == "Director" and person.get("name")
            ]
            self._director_text = (
                f"Director: {', '.join(directors) or '—'}"
            )
        cast = credits.get("cast") or []
        self._cast_names = [
            person.get("name", "")
            for person in cast[:5]
            if person.get("name")
        ]
        self._overview_text = (
            result.get("overview") or "No overview available."
        )
        self.overview_var.set(self._overview_text)
        self._resize_details_text()
        poster_path = result.get("poster_path")
        self._details_poster_path = poster_path
        self.details_poster.configure(
            image="",
            text="No\nposter" if not poster_path else "Loading…",
            width=11,
            height=7,
        )
        self.details_poster.image = None
        if poster_path:
            result_index = self.selected_index
            if result_index is not None:
                self._request_details_poster(result_index, poster_path)

        if self.is_tv:
            episode_title = result.get("episode_title")
            season = result.get(
                "_episode_season_number", self.item.season_number,
            )
            episode = result.get(
                "_episode_number", self.item.episode_number,
            )
            episode_end = result.get(
                "_episode_end_number", self.item.episode_end_number,
            )
            if (
                season is not None and episode is not None
            ):
                current = f"S{season:02d}E{episode:02d}"
                if isinstance(episode_end, int) and episode_end > episode:
                    current += f"-E{episode_end:02d}"
                if episode_title:
                    current += f" · {episode_title}"
            else:
                current = "Season and episode information unavailable."
            self.tv_info_var.set(current)
            if result.get("_episode_error"):
                self.search_status_var.set(result["_episode_error"])
            elif not episode_title:
                self.search_status_var.set("Loading episode details…")
            self.update_button.configure(
                state=(
                    "normal"
                    if (
                        result.get("id")
                        and self.item.season_number is not None
                        and self.item.episode_number is not None
                        and not result.get("_absolute_unmapped")
                    )
                    else "disabled"
                ),
            )
        else:
            self.update_button.configure(
                state="normal" if result.get("id") else "disabled",
            )

    def _clear_details(self):
        self.details_title_var.set("Select a result")
        self.details_id_var.set("")
        self.confidence_var.set("")
        self.genres_var.set("")
        self.director_var.set("")
        self.cast_var.set("")
        self._genre_text = ""
        self._director_text = ""
        self._cast_names = []
        self._overview_text = ""
        self.overview_var.set("")
        self.tv_info_var.set("")
        self.details_poster.configure(
            image="", text="No\nposter", width=11, height=7,
        )
        self.details_poster.image = None
        if hasattr(self, "update_button"):
            self.update_button.configure(state="disabled")

    def _update_results_scrollregion(self, _event=None):
        self.results_canvas.configure(
            scrollregion=self.results_canvas.bbox("all"),
        )

    def _resize_results_list(self, event):
        self.results_canvas.itemconfigure(
            self._results_window, width=event.width,
        )
        self._fit_results_window()

    def _fit_results_window(self, _event=None):
        if not hasattr(self, "results_canvas"):
            return
        view_height = self.results_canvas.winfo_height()
        content_height = self.results_cards.winfo_reqheight()
        window_height = max(view_height, content_height)
        self.results_canvas.itemconfigure(
            self._results_window,
            width=max(1, self.results_canvas.winfo_width()),
            height=max(1, window_height),
        )
        self._update_results_scrollregion()

    def _resize_details_text(self, _event=None):
        content_width = self.details_content.winfo_width()
        poster_width = max(
            DETAIL_POSTER_SIZE[0], self.details_poster.winfo_reqwidth(),
        )
        side_width = max(
            140, content_width - 20 - poster_width - 9,
        )
        full_width = max(190, content_width - 24)
        for label in (
            self.details_title_label, self.genres_label,
            self.director_label, self.cast_label,
        ):
            label.configure(wraplength=side_width)
        self.genres_var.set(self._fit_detail_line(self._genre_text, side_width))
        self.director_var.set(
            self._fit_detail_line(self._director_text, side_width),
        )
        self.cast_var.set(self._format_cast(self._cast_names, side_width))
        self.overview_label.configure(wraplength=full_width)
        self.overview_var.set(self._fit_overview(self._overview_text, full_width))
        self.tv_info_label.configure(wraplength=full_width)

    def _fit_overview(self, text, width, max_lines=3):
        if not text:
            return ""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self._details_font.measure(candidate) > width:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    last_line = lines[-1]
                    while last_line and self._details_font.measure(
                        f"{last_line.rstrip()}...",
                    ) > width:
                        last_line = last_line[:-1]
                    lines[-1] = f"{last_line.rstrip()}..."
                    return "\n".join(lines)
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines)

    def _fit_detail_line(self, text, width):
        if not text or self._details_font.measure(text) <= width:
            return text
        shortened = text
        while shortened and self._details_font.measure(
            f"{shortened.rstrip()}…",
        ) > width:
            shortened = shortened[:-1]
        return f"{shortened.rstrip()}…" if shortened else "…"

    def _format_cast(self, names, width):
        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            return "Cast: —"
        lines = ["Cast:"]
        included = 0
        overflow = False
        for name in names:
            candidate = f"{lines[0]}, {name}" if lines[0] != "Cast:" else (
                f"Cast: {name}"
            )
            if self._details_font.measure(candidate) <= width:
                lines[0] = candidate
                included += 1
                continue
            if len(lines) == 1:
                lines.append(self._fit_detail_line(name, width))
                included += 1
                overflow = lines[1] != name
                if overflow:
                    break
                continue
            candidate = f"{lines[1]}, {name}"
            if self._details_font.measure(candidate) <= width:
                lines[1] = candidate
                included += 1
                continue
            overflow = True
            break
        if overflow or included < len(names):
            suffix = lines[-1].rstrip(", ") + "…"
            lines[-1] = self._fit_detail_line(suffix, width)
        return "\n".join(lines[:2])

    def _bind_results_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind(
            "<Button-4>",
            lambda _event: self._scroll_results_from_mouse(-1),
            add="+",
        )
        widget.bind(
            "<Button-5>",
            lambda _event: self._scroll_results_from_mouse(1),
            add="+",
        )
        for child in widget.winfo_children():
            self._bind_results_mousewheel(child)

    def _on_mousewheel(self, event):
        delta = event.delta
        units = -int(delta / 120) if abs(delta) >= 120 else (
            -1 if delta > 0 else 1 if delta < 0 else 0
        )
        self._scroll_results(units)
        return "break"

    def _scroll_results_from_mouse(self, units):
        self._scroll_results(units)
        return "break"

    def _scroll_results(self, units):
        if units:
            self.results_canvas.yview_scroll(units, "units")

    @staticmethod
    def _cache_image(image_data, size):
        with Image.open(io.BytesIO(image_data)) as source:
            image = source.convert("RGB")
        image.thumbnail(size)
        return ImageTk.PhotoImage(image)

    def _poll_results(self):
        if self._closed:
            return
        self._poll_after_id = None
        try:
            while True:
                result = self._result_queue.get_nowait()
                if result[0] == "search":
                    _, generation, query, results, error = result
                    self._show_search_results(
                        generation, query, results, error,
                    )
                elif result[0] == "id":
                    _, generation, tmdb_id, details, error = result
                    self._show_id_result(
                        generation, tmdb_id, details, error,
                    )
                elif result[0] == "poster":
                    _, generation, index, path, data, error = result
                    self._load_poster_result(
                        generation, index, path, data, error,
                    )
                elif result[0] == "episode":
                    (
                        _, generation, tmdb_id, episode_title, error,
                        coordinates,
                    ) = result
                    if (
                        generation != self._search_generation
                        or not self.selected_result
                        or self.selected_result.get("id") != tmdb_id
                        or not self.selected_details
                    ):
                        continue
                    self.selected_details["episode_title"] = episode_title
                    self.selected_details["_episode_error"] = error
                    self.selected_details["_absolute_unmapped"] = bool(
                        error
                        and error.startswith("Absolute episode number")
                    )
                    if coordinates:
                        (
                            self.selected_details["_episode_season_number"],
                            self.selected_details["_episode_number"],
                            self.selected_details["_episode_end_number"],
                        ) = coordinates
                    self._show_selected_details(self.selected_details)
                    if error:
                        self.search_status_var.set(error)
                else:
                    _, generation, tmdb_id, details, error = result
                    if (
                        generation != self._search_generation
                        or not self.selected_result
                        or self.selected_result.get("id") != tmdb_id
                    ):
                        continue
                    if (
                        self.is_tv
                        and self.selected_details
                        and self.selected_details.get("episode_title")
                        and not details.get("episode_title")
                    ):
                        details["episode_title"] = (
                            self.selected_details["episode_title"]
                        )
                    if self.is_tv and self.selected_details:
                        for key in (
                            "_episode_season_number", "_episode_number",
                            "_episode_end_number", "_absolute_unmapped",
                            "_episode_error",
                        ):
                            if key in self.selected_details:
                                details[key] = self.selected_details[key]
                    self.selected_details = details
                    self._show_selected_details(details)
                    if error:
                        self.search_status_var.set(
                            f"Details unavailable: {error}",
                        )
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_after_id = self.after(100, self._poll_results)

    def update_match(self):
        if not self.selected_result or not self.selected_details:
            return
        if self.is_tv:
            choice = to_tv_choice(
                self.selected_details,
                self.selected_details.get(
                    "_episode_season_number", self.item.season_number,
                ),
                self.selected_details.get(
                    "_episode_number", self.item.episode_number,
                ),
                self.selected_details.get("episode_title")
                or self.item.episode_title,
                self.selected_details.get(
                    "_episode_end_number", self.item.episode_end_number,
                ),
            )
        else:
            choice = to_choice(self.selected_details)
        choice["genres"] = self.selected_details.get("genres", [])
        if self.is_tv:
            self.item._pending_manual_show = (
                choice, dict(self.selected_details),
            )
        else:
            self.item._pending_manual_show = None
        self.app.set_match(
            self.item, choice, "manual", source="manual",
        )
        self._close("matched")

    def cancel(self):
        self._close("cancelled")

    def keep_current(self):
        self._close("kept")

    def _close(self, outcome="cancelled"):
        if self._closed:
            return
        self._closed = True
        if self._search_after_id:
            self.after_cancel(self._search_after_id)
            self._search_after_id = None
        if self._poll_after_id:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        self._cancel_event.set()
        if self._owns_operation_cache:
            self.operation_cache.discard()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
        if self.on_close:
            self.on_close(outcome)
