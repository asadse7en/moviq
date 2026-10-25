import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ..controls import SegmentedModeToggle
from ._common import check_tmdb_key


class SettingsSupportMixin:
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
            "Disabled": self.app.pal["muted"],
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
            "Needs a key": self.app.pal["muted"],
            "Not checked": self.app.pal["muted"],
        }
        self.key_status_var.set(status)
        self.key_status_label.configure(
            foreground=colors.get(
                status,
                colors["Rejected"] if status.startswith("Couldn't save")
                else self.app.pal["muted"],
            ),
        )
        if "tmdb" in getattr(self, "_provider_status_dots", {}):
            self._style_provider_status("tmdb")

    def _key_draft_changed(self, *_args):
        key = self.key_entry_var.get()
        if self.vars["key"].get() != key:
            self.vars["key"].set(key)
        if self._saved_key_status_after is not None:
            self.after_cancel(self._saved_key_status_after)
            self._saved_key_status_after = None
            self._saved_key_validation_status = None
        self._key_generation += 1
        if "tmdb" not in self.app.provider_settings.enabled:
            self._set_key_status("Disabled")
            return
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
            self._key_after = None
        if "tmdb" not in self.app.provider_settings.enabled:
            self._set_key_status("Disabled")
            return
        key = self.key_entry_var.get().strip()
        generation = self._key_generation
        if not key:
            self._set_key_status("Needs a key")
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
        if "tmdb" not in self.app.provider_settings.enabled:
            self._set_key_status("Disabled")
            return
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
