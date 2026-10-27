import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from ..controls import RoundedCard, SegmentedModeToggle
from ._common import check_tmdb_key


class SettingsSupportMixin:
    def _build_safety(self, frame):
        self._section_header(
            frame, "Files & Safety",
            "Choose how matching and file changes are handled.",
            bottom_pad=22,
        )
        self._safety_surfaces = []
        self._safety_labels = []
        cards = tk.Frame(frame, background=self.app.pal["header_bg"])
        self._safety_surfaces.append((cards, "page"))
        cards.pack(fill="x", pady=(0, 12))
        cards.columnconfigure(0, weight=1, uniform="safety_cards")
        cards.columnconfigure(1, weight=1, uniform="safety_cards")

        matching_card = self._safety_card(cards, "Automatic matching")
        matching_card.grid(
            row=0, column=0, sticky="nsew", padx=(0, 6),
        )
        matching_content = matching_card.content
        self._safety_setting_row(
            matching_content, 1, "Auto-match new files",
            "Match files as soon as they are added.",
            self.vars["auto_match"],
        )
        self._safety_divider(matching_content, 3)
        self._safety_text(
            matching_content, "Match confidence threshold",
            foreground="normal", font=("TkDefaultFont", 10, "bold"),
        ).grid(
            row=4, column=0, sticky="w", padx=(10, 8), pady=(8, 5),
        )
        self.threshold_label_var = tk.StringVar()
        self._safety_text(
            matching_content, textvariable=self.threshold_label_var,
            foreground="accent", font=("TkDefaultFont", 10, "bold"),
        ).grid(row=4, column=1, sticky="e", padx=(8, 10), pady=(8, 5))
        scale_wrap = tk.Frame(
            matching_content, background=self.app.pal["preset_surface"],
        )
        self._safety_surfaces.append((scale_wrap, "card"))
        scale_wrap.grid(
            row=5, column=0, columnspan=2, sticky="ew",
            padx=10, pady=(0, 2),
        )
        scale_wrap.columnconfigure(0, weight=1)
        scale = ttk.Scale(
            scale_wrap, from_=50, to=100, variable=self.vars["threshold"],
            command=self._threshold_moved, length=300,
            style="Moviq.Horizontal.TScale",
        )
        scale.grid(row=0, column=0, sticky="ew")
        self.threshold_hint_var = tk.StringVar()
        self._safety_text(
            matching_content, textvariable=self.threshold_hint_var,
            foreground="muted",
        ).grid(
            row=6, column=0, columnspan=2, sticky="w",
            padx=10, pady=(0, 12),
        )
        self.vars["threshold"].trace_add(
            "write", lambda *_args: self._update_threshold_hint(),
        )
        self._update_threshold_hint()

        operations_card = self._safety_card(cards, "File operations")
        operations_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        operations_content = operations_card.content
        for index, (title, description, key) in enumerate((
            (
                "Confirm before renaming",
                "Review planned changes before applying.",
                "confirm_apply",
            ),
            (
                "Put each movie in its own folder",
                "Keep each movie in a separate folder.",
                "movie_folder",
            ),
            (
                "Rename subtitles with the video",
                "Keep attached subtitle names in sync.",
                "rename_subtitles",
            ),
        )):
            row = 1 + index * 3
            self._safety_setting_row(
                operations_content, row, title, description, self.vars[key],
            )
            if index < 2:
                self._safety_divider(operations_content, row + 2)

        library_card = self._safety_card(frame, "Library destination")
        self.library_card = library_card
        library_card.pack(fill="x", pady=(0, 14))
        library_content = library_card.content
        library_content.columnconfigure(0, weight=1)
        self._safety_text(
            library_content,
            "Choose where renamed files should go.",
            foreground="muted",
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))
        self.library_selector = SegmentedModeToggle(
            library_content,
            self.vars["library_mode"].get(),
            self._select_library_mode,
            ui_scale=self.app.UI_SCALE,
            labels=("In place", "Move to library"),
            values=("in_place", "library"),
        )
        self.library_selector.grid(
            row=0, column=1, rowspan=2, sticky="e",
            padx=(16, 10), pady=(8, 0),
        )
        self.library_mode_hint = self._safety_text(
            library_content,
            "Files stay in their current folders.",
            foreground="muted",
        )
        self.library_mode_hint.grid(
            row=2, column=0, sticky="w", padx=10, pady=(0, 10),
        )
        self._safety_divider(library_content, 3)
        self.library_paths_frame = tk.Frame(
            library_content, background=self.app.pal["preset_surface"],
        )
        self._safety_surfaces.append((self.library_paths_frame, "card"))
        self.library_paths_frame.grid(
            row=4, column=0, columnspan=2, sticky="ew",
            padx=10, pady=(8, 10),
        )
        self.library_paths_frame.columnconfigure(1, weight=1)
        self._safety_text(
            self.library_paths_frame,
            "Library folders",
            foreground="normal", font=("TkDefaultFont", 10, "bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
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

    def _safety_card(self, parent, title):
        palette = self.app.pal
        card = RoundedCard(
            parent, palette["header_bg"], palette["preset_surface"],
            palette["preset_border"],
        )
        self._safety_surfaces.append((card, "rounded_card"))
        card.content.columnconfigure(0, weight=1)
        self._safety_text(
            card.content, title, foreground="normal",
            font=("TkDefaultFont", 11, "bold"),
        ).grid(
            row=0, column=0, columnspan=2, sticky="w",
            padx=10, pady=(10, 6),
        )
        return card

    def _safety_text(self, parent, text="", foreground="normal", **kwargs):
        palette = self.app.pal
        font = kwargs.pop("font", "TkDefaultFont")
        label = tk.Label(
            parent, text=text, anchor="w",
            background=palette["preset_surface"],
            foreground=(
                palette["fg"] if foreground == "normal"
                else palette["muted"] if foreground == "muted"
                else palette["accent"]
            ),
            font=font, **kwargs,
        )
        self._safety_labels.append((label, foreground))
        return label

    def _safety_setting_row(self, card, row, title, description, variable):
        self._safety_text(
            card, title, foreground="normal",
            font=("TkDefaultFont", 10, "bold"),
        ).grid(row=row, column=0, sticky="w", padx=(10, 8), pady=(9, 1))
        self._safety_text(
            card, description, foreground="muted",
        ).grid(row=row + 1, column=0, sticky="w", padx=(10, 8), pady=(0, 9))
        self._switch(
            card, variable, style="Settings.Safety.Switch.TCheckbutton",
        ).grid(row=row, column=1, rowspan=2, sticky="e", padx=(8, 10), pady=9)

    def _safety_divider(self, parent, row, columnspan=2):
        divider = tk.Frame(
            parent, height=1, background=self.app.pal["preset_border"],
        )
        self._safety_surfaces.append((divider, "divider"))
        divider.grid(
            row=row, column=0, columnspan=columnspan, sticky="ew",
            padx=18,
        )

    def _threshold_moved(self, value):
        snapped = min(100, max(50, int(round(float(value) / 5) * 5)))
        if self.vars["threshold"].get() != snapped:
            self.vars["threshold"].set(snapped)

    def _update_threshold_hint(self):
        threshold = min(
            100,
            max(50, int(round(self.vars["threshold"].get() / 5) * 5)),
        )
        if self.vars["threshold"].get() != threshold:
            self.vars["threshold"].set(threshold)
        self.threshold_label_var.set(f"{threshold}%")
        if threshold >= 90:
            hint = "Strict: fewer matches are accepted automatically."
        elif threshold >= 75:
            hint = "Balanced: uncertain matches go to review."
        else:
            hint = "Lenient: more matches are accepted automatically."
        self.threshold_hint_var.set(hint)

    def _select_library_mode(self, mode):
        self.vars["library_mode"].set(mode)

    def _set_library_selector_theme(self, palette):
        self.library_selector.set_theme(
            background=palette["preset_surface"],
            track=palette["switch_track"],
            border=palette["switch_border"],
            active=palette["switch_active"],
            foreground=palette["fg"],
            muted=palette["header_muted"],
            accent_foreground=palette["header_accent"],
            canvas_background=palette["preset_surface"],
            active_border=palette["header_accent"],
        )
        self.library_selector.set_mode(
            self.vars["library_mode"].get(), animate=False,
        )

    def _update_library_mode(self):
        mode = self.vars["library_mode"].get()
        self.library_selector.set_mode(mode)
        self._show_library_paths()

    def _library_path_row(self, parent, row, label, variable, setting):
        self._safety_text(
            parent, label, foreground="muted",
        ).grid(
            row=row, column=0, sticky="w", padx=(0, 12), pady=5,
        )
        ttk.Entry(
            parent, textvariable=variable, state="readonly",
            style="Settings.Safety.TEntry",
        ).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(
            parent, text="Browse", style="Settings.TButton",
            command=lambda var=variable, key=setting: self._browse_library(
                var, key,
            ),
        ).grid(row=row, column=2, sticky="e", padx=(8, 0), pady=5)

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
        self.library_card.after_idle(self.library_card._fit_height)

    def recolor_safety(self, palette):
        for widget, role in getattr(self, "_safety_surfaces", ()):
            if role == "divider":
                widget.configure(background=palette["preset_border"])
            elif role == "page":
                widget.configure(background=palette["header_bg"])
            elif role == "rounded_card":
                widget.set_surface(
                    palette["header_bg"], palette["preset_surface"],
                    palette["preset_border"],
                )
            else:
                widget.configure(background=palette["preset_surface"])
        for label, foreground in getattr(self, "_safety_labels", ()):
            label.configure(
                background=palette["preset_surface"],
                foreground=(
                    palette["fg"] if foreground == "normal"
                    else palette["muted"] if foreground == "muted"
                    else palette["accent"]
                ),
            )

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
