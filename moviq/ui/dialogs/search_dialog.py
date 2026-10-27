import queue
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from ...core.matching import (
    score, to_choice, to_tv_choice, tv_score, tv_year_of, year_of,
)
from ...core.naming import similarity
from ...core.show_resolver import ShowState
from ...core.providers.ids import display_id, parse_typed_id
from ...core.tmdb import MAX_WORKERS, TMDBOperationCache
from ..popups import apply_titlebar_theme, set_window_icon
from ..icons import draw_icon
from ._common import ID_RE, _match_dialog_sizes
from .details_panel import DetailsPanelMixin
from .poster_loader import PosterLoaderMixin
from .results_list import ResultsListMixin


class SearchDialog(
    ResultsListMixin, DetailsPanelMixin, PosterLoaderMixin, tk.Toplevel,
):
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
            text=(
                "Search title, ID (tmdb:603 or imdb:tt0111161), or "
                "season/episode..."
            ),
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
        draw_icon(
            self.search_icon, "search", 18, palette["muted"], (9, 9),
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
        self._set_match_button_state(False)
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
        foreground = (
            palette["accent_text"] if is_primary else palette["fg"]
        )
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
            disabledforeground=(
                palette["muted"] if is_primary else foreground
            ),
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
            lambda _event: button.configure(
                bg=background,
            ) if str(button.cget("state")) != "disabled" else None,
        )
        return button

    def _set_match_button_state(self, enabled):
        palette = self.app.pal
        self.update_button.configure(
            state="normal" if enabled else "disabled",
            bg=palette["accent"] if enabled else palette["btn"],
            fg=palette["accent_text"] if enabled else palette["muted"],
            disabledforeground=palette["muted"],
            cursor="hand2" if enabled else "arrow",
        )

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

    @staticmethod
    def _split_query(query):
        year_matches = list(
            re.finditer(r"(?<!\d)(?:18|19|20|21)\d{2}(?!\d)", query)
        )
        if not year_matches:
            return query.strip(), None
        year_match = year_matches[-1]  # "1917 2019" -> title 1917, year 2019
        title = (
            query[:year_match.start()] + " " + query[year_match.end():]
        ).strip(" \t-–—,.:()[]{}")
        if not title:
            return query.strip(), None
        return title, int(year_match.group())

    @staticmethod
    def _parse_tmdb_id(query):
        text = query.strip()
        typed = parse_typed_id(text)  # tmdb:603, imdb:tt0111161, ...
        if typed is not None:
            return typed
        match = ID_RE.fullmatch(text)
        if not match:
            return None
        digits = match.group(1)
        if (
            not text.lower().startswith("tmdb")
            and re.fullmatch(r"(?:18|19|20|21)\d{2}", digits)
        ):
            return None  # 1917, 2020, 1883: search as a title; use "tmdb:1917" for the ID
        return int(digits)

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
        self._set_match_button_state(False)
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
            self.search_status_var.set(f"Looking up {display_id(tmdb_id)}…")
            self._executor.submit(
                self._load_by_id, generation, tmdb_id, self.is_tv,
            )
            return
        self._direct_id_search = False
        title, year = self._split_query(query)
        if not title:
            self.search_status_var.set("Type a title to search.")
            return
        self.search_status_var.set("Searching…")
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
                    self.selected_details[
                        "_episode_mapping_pending"
                    ] = False
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
                            "_episode_error", "_episode_mapping_pending",
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
