import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, ttk

from ._common import TMDB_KEY_URL, TMDB_SIGNUP_URL, check_tmdb_key


class GeneralTabMixin:
    def _build_general(self, frame):
        self._section_header(
            frame, "General", "Manage your TMDB connection and appearance.",
        )
        group = self._group(frame, "TMDB")
        self.key_status_var = tk.StringVar(value="● Not checked")
        self.key_entry_var = tk.StringVar(value=self.vars["key"].get())
        key_row = ttk.Frame(group)
        key_row.grid(row=0, column=1, sticky="e", pady=5)
        self.key_entry_wrap, self.key_entry = self.make_styled_entry(
            key_row, self.key_entry_var, width=30, show="•",
        )
        self.key_eye_canvas = tk.Canvas(
            self.key_entry_wrap, width=28, height=28,
            background=self.app.pal["preset_surface"],
            highlightthickness=0, cursor="hand2", takefocus=1,
        )
        self.key_eye_canvas.grid(
            row=0, column=1, sticky="e", padx=(0, 5), pady=2,
        )
        self.key_eye_canvas.create_line(
            3, 14, 7, 8, 14, 5, 21, 8, 25, 14, 21, 20, 14, 23, 7, 20,
            smooth=True, splinesteps=20, fill=self.app.pal["muted"],
            width=2, tags="eye",
        )
        self.key_eye_canvas.create_oval(
            11, 11, 17, 17, fill=self.app.pal["muted"],
            outline=self.app.pal["muted"], tags="pupil",
        )
        self.key_eye_canvas.create_line(
            5, 23, 23, 5, fill=self.app.pal["muted"], width=2,
            state="hidden", tags="slash",
        )
        self.key_eye_canvas.bind(
            "<Button-1>", lambda _event: self._toggle_key_visibility(),
        )
        self.key_eye_canvas.bind(
            "<space>", lambda _event: self._toggle_key_visibility(),
        )
        self.key_entry.bind("<<Copy>>", self._copy_api_key, add="+")
        self.key_entry.bind("<Control-z>", self._undo_key_entry)
        self.key_entry.bind("<Control-y>", self._redo_key_entry)
        self.key_entry_wrap.pack(side="left")
        ttk.Button(
            key_row, text="Test", style="Settings.TButton",
            command=self.test_key,
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            key_row, text="Save", style="Settings.TButton",
            command=self.save_key,
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
        self._link(
            group, "Get a TMDB API key", TMDB_KEY_URL,
        ).grid(row=1, column=0, sticky="w", padx=(0, 18), pady=(0, 5))
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
