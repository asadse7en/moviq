import tkinter as tk
from tkinter import ttk

from ..controls import RoundedCard, SegmentedModeToggle
from ._common import (
    CUSTOM_ID, MOVIE_DEFAULT_ID, MOVIE_FORMATS, MOVIE_SAMPLE,
    TV_DEFAULT_ID, TV_FORMATS, TV_SAMPLE, format_pattern, render_pattern,
)


class FormattingTabMixin:
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
            track=palette["switch_track"],
            border=palette["switch_border"],
            active=palette["switch_active"],
            foreground=palette["fg"],
            muted=palette["header_muted"],
            accent_foreground=palette["header_accent"],
            canvas_background=palette["header_bg"],
            active_border=palette["header_accent"],
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
        right_panel = RoundedCard(
            right_pane, self.app.pal["bg"],
            self.app.pal["preset_surface"],
            self.app.pal["preset_border"],
        )
        right_panel.grid(row=1, column=0, sticky="nsew")
        panel_content = right_panel.content
        panel_content.columnconfigure(0, weight=1)
        self._format_panels[kind] = right_panel
        self._format_panel_labels[kind] = {
            "primary": [], "muted": [], "accent": [],
        }
        self._format_panel_surfaces[kind] = [panel_content]
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
                panel_content, background=self.app.pal["preset_surface"],
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
                        "Moviq.Formatting.Switch.TCheckbutton"
                        if self.app.have_sv else "Formatting.TCheckbutton"
                    ),
                )
                checkbox.grid(row=0, column=1, sticky="e", padx=(8, 0))
                self._format_folder_toggles[kind][create_key] = checkbox
            entry_wrap, entry = self.make_styled_entry(
                panel_content, field_variable,
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
        token_bar = ttk.Frame(
            panel_content, style="Settings.Card.TFrame",
        )
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
        ttk.Separator(
            panel_content, orient="horizontal",
            style="Settings.Card.TSeparator",
        ).grid(
            row=token_row + 1, column=0, sticky="ew", pady=(2, 7),
        )
        preview_heading = tk.Label(
            panel_content, text="Preview", font="TkHeadingFont",
            background=self.app.pal["preset_surface"],
            foreground=self.app.pal["fg"],
        )
        preview_heading.grid(
            row=token_row + 2, column=0, sticky="w", pady=(0, 3),
        )
        self._format_panel_labels[kind]["primary"].append(preview_heading)
        preview = tk.Frame(
            panel_content, background=self.app.pal["preset_surface"],
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
        self._format_panels[kind].set_surface(
            palette["bg"], surface, palette["preset_border"],
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
        card = RoundedCard(
            parent, self.app.pal["bg"], self.app.pal["preset_surface"],
            self.app.pal["preset_border"], radius=7,
        )
        content = card.content
        card.configure(takefocus=True)
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
            record["card"].set_surface(
                palette["bg"], background, border,
            )
            record["card"].configure(cursor="hand2")
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
