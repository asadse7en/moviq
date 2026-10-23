import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

from ..controls import SegmentedModeToggle
from ...core.providers.settings import PROVIDER_INFO
from ._common import TMDB_KEY_URL, TMDB_SIGNUP_URL, check_tmdb_key


class GeneralTabMixin:
    def _build_general(self, frame):
        self._section_header(
            frame, "General", "Choose a metadata source and manage appearance.",
        )
        source_group = self._group(frame, "Matching source")
        source_values = ("Automatic",) + tuple(
            info["label"] for info in PROVIDER_INFO.values()
        )
        source_labels = {
            name: info["label"] for name, info in PROVIDER_INFO.items()
        }
        selected = self.app.provider_settings.selected
        source_value = (
            "Automatic" if selected == "automatic"
            else source_labels[selected]
        )
        self.source_selection_var = tk.StringVar(value=source_value)
        source_selector = ttk.Combobox(
            source_group, textvariable=self.source_selection_var,
            values=source_values, state="readonly", width=20,
        )
        source_selector.bind(
            "<<ComboboxSelected>>", self._source_selection_changed,
        )
        self._row(
            source_group, 0, "Source", source_selector,
            "Automatic uses enabled sources in priority order. "
            "A selected source is used alone.",
        )
        appearance = self._group(frame, "Appearance")
        theme_controls = ttk.Frame(appearance)
        self._theme_option_indicators = {}
        for text, value in (
            ("Light", "light"), ("Dark", "dark"), ("System", "system"),
        ):
            option = ttk.Frame(theme_controls)
            indicator = tk.Canvas(
                option, width=18, height=18,
                background=self.app.pal["bg"],
                highlightthickness=0, bd=0, takefocus=1,
                cursor="hand2",
            )
            ring = indicator.create_oval(
                3, 3, 15, 15, width=2,
            )
            dot = indicator.create_oval(7, 7, 11, 11, outline="")
            indicator.pack(side="left", padx=(0, 4))
            label = self._label(option, text, cursor="hand2")
            label.pack(side="left")
            option.pack(side="left", padx=(0, 12))
            self._theme_option_indicators[value] = (
                indicator, ring, dot,
            )
            choose = lambda _event=None, selected=value: (
                self._select_theme_option(selected)
            )
            for control in (option, indicator, label):
                control.bind("<Button-1>", choose)
            indicator.bind("<space>", choose)
            indicator.bind("<Return>", choose)
            for key, direction in (
                ("<Left>", -1), ("<Up>", -1),
                ("<Right>", 1), ("<Down>", 1),
            ):
                indicator.bind(
                    key,
                    lambda _event, selected=value, step=direction: (
                        self._move_theme_option(selected, step)
                    ),
                )
        self._update_theme_options()
        self._row(appearance, 0, "Theme", theme_controls)

    def _source_selection_changed(self, _event=None):
        selected_label = self.source_selection_var.get()
        selected = "automatic"
        if selected_label != "Automatic":
            selected = next(
                name for name, info in PROVIDER_INFO.items()
                if info["label"] == selected_label
            )
        self.app.set_provider_settings(
            self.app.provider_settings.with_selected(selected),
        )
        return "break"

    def _select_theme_option(self, value):
        self.vars["theme"].set(value)
        self._update_theme_options()
        return "break"

    def _move_theme_option(self, value, direction):
        options = tuple(self._theme_option_indicators)
        index = (options.index(value) + direction) % len(options)
        selected = options[index]
        self._theme_option_indicators[selected][0].focus_set()
        return self._select_theme_option(selected)

    def _update_theme_options(self, palette=None):
        palette = palette or self.app.pal
        selected = self.vars["theme"].get()
        for value, (indicator, ring, dot) in (
            self._theme_option_indicators.items()
        ):
            is_selected = value == selected
            indicator.configure(background=palette["bg"])
            indicator.itemconfigure(
                ring,
                outline=(
                    palette["accent"] if is_selected
                    else palette["header_border_hi"]
                ),
            )
            indicator.itemconfigure(
                dot,
                fill=palette["accent"],
                state="normal" if is_selected else "hidden",
            )

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
        text.insert("end", " \u2192 go to ")
        text.insert("end", "Sources", ("bold",))
        text.insert("end", " \u2192 copy your ")
        text.insert("end", "API Key", ("api_link", "bold"))
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
        library_group = self._group(frame, "Library destination")
        library_group.columnconfigure(0, weight=1)
        self._label(
            library_group,
            "Choose where renamed files should go.",
            font="TkHeadingFont",
        ).grid(row=0, column=0, sticky="w", pady=(0, 2))
        self.library_selector = SegmentedModeToggle(
            library_group,
            self.vars["library_mode"].get(),
            self._select_library_mode,
            ui_scale=self.app.UI_SCALE,
            labels=("In place", "Move to library"),
            values=("in_place", "library"),
        )
        self.library_selector.grid(
            row=0, column=1, rowspan=2, sticky="e", padx=(16, 0),
        )
        self.library_mode_hint = self._label(
            library_group,
            "Files stay in their current folders.",
            muted=True,
        )
        self.library_mode_hint.grid(row=1, column=0, sticky="w")
        ttk.Separator(library_group).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(12, 8),
        )
        self.library_paths_frame = ttk.Frame(library_group)
        self.library_paths_frame.grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(2, 4),
        )
        self.library_paths_frame.columnconfigure(1, weight=1)
        self._label(
            self.library_paths_frame,
            "Library folders",
            font="TkHeadingFont",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self._library_path_row(
            self.library_paths_frame, 1, "Movies folder",
            self.vars["movies_library"], "movies_library",
        )
        self._library_path_row(
            self.library_paths_frame, 2, "TV shows folder",
            self.vars["tv_library"], "tv_library",
        )
        self.vars["library_mode"].trace_add(
            "write", lambda *_args: self._update_library_mode(),
        )
        self._set_library_selector_theme(self.app.pal)
        self._show_library_paths()

    def _select_library_mode(self, mode):
        self.vars["library_mode"].set(mode)

    def _set_library_selector_theme(self, palette):
        self.library_selector.set_theme(
            background=palette["header_bg"],
            track=palette["header_bg"],
            border=palette["header_accent"],
            active=palette["header_accent"],
            foreground=palette["fg"],
            muted=palette["header_muted"],
            accent_foreground=palette["accent_text"],
            canvas_background=palette["header_bg"],
        )
        self.library_selector.set_mode(
            self.vars["library_mode"].get(), animate=False,
        )

    def _update_library_mode(self):
        mode = self.vars["library_mode"].get()
        self.library_selector.set_mode(mode)
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
        is_library = self.vars["library_mode"].get() == "library"
        if is_library:
            self.library_mode_hint.configure(
                text="Organize renamed files under your chosen movie and TV folders.",
            )
            self.library_paths_frame.grid()
        else:
            self.library_mode_hint.configure(
                text="Files stay in their current folders.",
            )
            self.library_paths_frame.grid_remove()

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

    def _key_draft_changed(self, *_args):
        if self._saved_key_status_after is not None:
            self.after_cancel(self._saved_key_status_after)
            self._saved_key_status_after = None
            self._saved_key_validation_status = None
        self._key_generation += 1
        self._set_key_status("Not checked")
        self._schedule_key_check(800)

    def _record_key_entry_edit(self, *_args):
        value = self.key_entry_var.get()
        if self._key_history_suspended or value == self._key_history_value:
            return
        self._key_history.append(self._key_history_value)
        self._key_redo_history.clear()
        self._key_history_value = value

    def _undo_key_entry(self, _event=None):
        if not self._key_history:
            return "break"
        self._key_redo_history.append(self.key_entry_var.get())
        value = self._key_history.pop()
        self._key_history_suspended = True
        try:
            self.key_entry_var.set(value)
            self.key_entry.icursor("end")
        finally:
            self._key_history_suspended = False
        self._key_history_value = value
        return "break"

    def _redo_key_entry(self, _event=None):
        if not self._key_redo_history:
            return "break"
        self._key_history.append(self.key_entry_var.get())
        value = self._key_redo_history.pop()
        self._key_history_suspended = True
        try:
            self.key_entry_var.set(value)
            self.key_entry.icursor("end")
        finally:
            self._key_history_suspended = False
        self._key_history_value = value
        return "break"

    def save_key(self):
        key = self.key_entry_var.get().strip()
        self.vars["key"].set(self.key_entry_var.get())
        if self._key_after is not None:
            self.after_cancel(self._key_after)
            self._key_after = None
        if self._saved_key_status_after is not None:
            self.after_cancel(self._saved_key_status_after)
        self._key_generation += 1
        generation = self._key_generation
        if not key:
            self._saved_key_validation_status = "Not checked"
        elif not self._V3_KEY.fullmatch(key):
            self._saved_key_validation_status = "Wrong format"
        else:
            self._saved_key_validation_status = None
            self._submit_key_check(key, generation)
        self._set_key_status("Saved")
        self._saved_key_status_after = self.after(
            2500, self._restore_saved_key_status,
        )

    def _restore_saved_key_status(self):
        self._saved_key_status_after = None
        status = self._saved_key_validation_status or "Checking"
        self._saved_key_validation_status = None
        self._set_key_status(status)

    def _toggle_key_visibility(self):
        show = not bool(self.key_entry.cget("show") == "")
        self.key_entry.configure(show="" if show else "•")
        self.key_eye_canvas.itemconfigure(
            "slash", state="normal" if show else "hidden",
        )

    def _copy_api_key(self, _event=None):
        try:
            start = self.key_entry.index("sel.first")
            end = self.key_entry.index("sel.last")
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(self.key_entry_var.get()[start:end])
        return "break"

    def _schedule_key_check(self, delay):
        if self._key_after is not None:
            self.after_cancel(self._key_after)
        key = self.key_entry_var.get().strip()
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
        key = self.key_entry_var.get().strip()
        self._key_generation += 1
        generation = self._key_generation
        if not key:
            self._set_key_status("Not checked")
            return
        if not self._V3_KEY.fullmatch(key):
            self._set_key_status("Wrong format")
            return
        self._submit_key_check(key, generation)
