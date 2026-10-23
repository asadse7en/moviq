import tkinter as tk
from tkinter import ttk

from ...core.providers.router import test_provider
from ...core.providers.settings import PROVIDER_INFO
from ._common import TMDB_KEY_URL

_KEY_FIELDS = {
    "omdb": (("omdb", "API key"),),
}
_SOURCE_NAMES = {
    "tmdb": "TMDb",
    "tvmaze": "TVmaze",
    "anilist": "AniList",
    "omdb": "OMDb",
    "offline": "Offline guess",
}


class ProvidersTabMixin:
    """Sources page: priority order, on/off, keys and a connection test."""

    def _build_providers(self, frame):
        self._section_header(
            frame, "Sources",
            "Automatic mode tries these from top to bottom and uses the first "
            "good match.",
        )
        settings = self.app.provider_settings
        self._provider_blocks = {}
        self._provider_enabled = {}
        self._provider_key_vars = {}
        self._provider_key_entries = {}
        self._provider_key_eyes = {}
        self._provider_status = {}
        self._provider_status_labels = {}
        self._provider_position = {}
        self._provider_move_buttons = {}

        for name in settings.order:
            self._build_provider_group(frame, name, settings)

        self.vars["key"].trace_add(
            "write", lambda *_args: self._refresh_provider_labels(),
        )
        self._layout_providers()

    def _build_provider_group(self, frame, name, settings):
        source = _SOURCE_NAMES[name]
        info = PROVIDER_INFO[name]
        group = self._group(frame, source)
        group.columnconfigure(0, weight=1)
        group.columnconfigure(1, weight=0)
        self._provider_blocks[name] = group

        enabled = tk.BooleanVar(value=name in settings.enabled)
        enabled.trace_add(
            "write", lambda *_args, provider=name: (
                self._provider_toggled(provider)
            ),
        )
        self._provider_enabled[name] = enabled

        row = self._row(
            group, 0, f"Use {source}",
            self._switch(group, enabled),
        )
        description = ttk.Frame(group)
        description.grid(
            row=row, column=0, columnspan=2, sticky="w",
            padx=(0, 18), pady=(0, 7),
        )
        text = (
            "Guess from the file name when nothing else can help. "
            "Always sent to Review."
            if name == "offline"
            else (
                f"{info['kinds']}. "
                f"{'API key required.' if info['needs_key'] else 'No key needed.'}"
            )
        )
        self._label(description, text, muted=True).pack(side="left")
        if info["needs_key"]:
            self._link(description, "Get a key", info["key_url"]).pack(
                side="left", padx=(6, 0),
            )
        row += 1
        row = self._provider_separator(group, row)

        position = tk.StringVar()
        self._provider_position[name] = position
        self._label(
            group, "Priority", font="TkHeadingFont",
        ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        self._label(
            group, textvariable=position, muted=True,
        ).grid(
            row=row + 1, column=0, sticky="w", padx=(0, 18),
            pady=(0, 7),
        )
        buttons = ttk.Frame(group)
        buttons.grid(row=row, column=1, rowspan=2, sticky="e", pady=7)
        move_up = ttk.Button(
            buttons, text="Move up", style="Settings.TButton",
            command=lambda provider=name: self._move_provider(provider, -1),
        )
        move_up.pack(side="left", padx=(0, 6))
        move_down = ttk.Button(
            buttons, text="Move down", style="Settings.TButton",
            command=lambda provider=name: self._move_provider(provider, 1),
        )
        move_down.pack(side="left")
        self._provider_move_buttons[name] = (move_up, move_down)
        row += 2
        row = self._provider_separator(group, row)

        if name == "tmdb":
            self._build_tmdb_key(group, row)
            return

        if name in _KEY_FIELDS:
            fields = _KEY_FIELDS[name]
            key_field = fields[0][0]
            key_var = tk.StringVar(value=settings.key(key_field))
            self._provider_key_vars[key_field] = key_var
            key_var.trace_add(
                "write",
                lambda *_args, field=key_field: (
                    self._provider_key_edited(field)
                ),
            )
            controls = ttk.Frame(group)
            controls.grid(row=row, column=1, sticky="e", pady=5)
            wrap, entry = self.make_styled_entry(
                controls, key_var, width=24, show="•",
            )
            self._provider_key_entries[key_field] = entry
            eye = self._provider_eye_toggle(wrap, key_field)
            self._provider_key_eyes[key_field] = eye
            wrap.pack(side="left")
            ttk.Button(
                controls, text="Test", style="Settings.TButton",
                command=lambda provider=name: self._test_provider(provider),
            ).pack(side="left", padx=(6, 0))
            ttk.Button(
                controls, text="Save", style="Settings.TButton",
                command=lambda provider=name: (
                    self._commit_provider_keys(provider)
                ),
            ).pack(side="left", padx=(6, 0))
            self._label(
                group, f"{source} API key", font="TkHeadingFont",
            ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=5)
            self._add_provider_status(
                group, name, row + 1,
                "Needs a key" if not key_var.get().strip() else "Not checked",
            )
            row += 2
            row = self._provider_separator(group, row)

            return

        if name in ("tvmaze", "anilist"):
            self._label(
                group, "Connection", font="TkHeadingFont",
            ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=5)
            ttk.Button(
                group, text="Test", style="Settings.TButton",
                command=lambda provider=name: self._test_provider(provider),
            ).grid(row=row, column=1, sticky="e", pady=5)
            self._add_provider_status(group, name, row + 1, "Not checked")

    def _build_tmdb_key(self, group, row):
        self.key_status_var = tk.StringVar(value="● Not checked")
        self.key_entry_var = tk.StringVar(value=self.vars["key"].get())
        key_row = ttk.Frame(group)
        key_row.grid(row=row, column=1, sticky="e", pady=5)
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
            group, "API key", font="TkHeadingFont",
        ).grid(
            row=row, column=0, sticky="w", padx=(0, 18), pady=5,
        )
        self.key_help_text = self._build_api_key_help(group)
        self.key_status_label = self._label(
            group, textvariable=self.key_status_var, muted=True,
        )
        self._set_key_status("Not checked")
        self.key_status_label.grid(
            row=row + 1, column=1, sticky="e", pady=(0, 5),
        )
        self._link(
            group, "Get a TMDB API key", TMDB_KEY_URL,
        ).grid(
            row=row + 1, column=0, sticky="w", padx=(0, 18), pady=(0, 5),
        )
        self.key_help_text.grid(
            row=row + 2, column=0, columnspan=2, sticky="ew",
            padx=(0, 18), pady=(0, 8),
        )
        ttk.Separator(group).grid(
            row=row + 3, column=0, columnspan=2, sticky="ew",
            pady=(2, 8),
        )

    def _provider_eye_toggle(self, wrap, field):
        eye = tk.Canvas(
            wrap, width=28, height=28,
            background=self.app.pal["preset_surface"],
            highlightthickness=0, cursor="hand2", takefocus=1,
        )
        eye.grid(row=0, column=1, sticky="e", padx=(0, 5), pady=2)
        eye.create_line(
            3, 14, 7, 8, 14, 5, 21, 8, 25, 14, 21, 20, 14, 23, 7, 20,
            smooth=True, splinesteps=20, fill=self.app.pal["muted"],
            width=2, tags="eye",
        )
        eye.create_oval(
            11, 11, 17, 17, fill=self.app.pal["muted"],
            outline=self.app.pal["muted"], tags="pupil",
        )
        eye.create_line(
            5, 23, 23, 5, fill=self.app.pal["muted"], width=2,
            state="hidden", tags="slash",
        )
        eye.bind(
            "<Button-1>",
            lambda _event, key=field: self._toggle_provider_key_visibility(key),
        )
        eye.bind(
            "<space>",
            lambda _event, key=field: self._toggle_provider_key_visibility(key),
        )
        return eye

    def _add_provider_status(self, group, name, row, status):
        variable = tk.StringVar()
        label = self._label(
            group, textvariable=variable, muted=True,
        )
        label.grid(
            row=row, column=0, sticky="w", padx=(0, 18), pady=(0, 7),
        )
        self._provider_status[name] = variable
        self._provider_status_labels[name] = label
        variable.trace_add(
            "write",
            lambda *_args, provider=name: (
                self._style_provider_status(provider)
            ),
        )
        self._set_provider_status(name, status)

    def _provider_separator(self, group, row):
        ttk.Separator(group).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(2, 8),
        )
        return row + 1

    def _layout_providers(self):
        settings = self.app.provider_settings
        for group in self._provider_blocks.values():
            group.pack_forget()
        for position, name in enumerate(settings.order):
            self._provider_blocks[name].pack(fill="x", pady=(0, 14))
            move_up, move_down = self._provider_move_buttons[name]
            move_up.configure(
                state="disabled" if position == 0 else "normal",
            )
            move_down.configure(
                state=(
                    "disabled"
                    if position == len(settings.order) - 1 else "normal"
                ),
            )
        self._refresh_provider_labels()

    def _refresh_provider_labels(self):
        settings = self.app.provider_settings
        for position, name in enumerate(settings.order, 1):
            self._provider_position[name].set(
                f"Position {position} of {len(settings.order)}",
            )

    def _set_provider_status(self, name, status):
        self._provider_status[name].set(f"• {status}")

    def _style_provider_status(self, name):
        status = self._provider_status[name].get()
        if not status.startswith("• "):
            status = f"• {status}"
            self._provider_status[name].set(status)
        status = status[2:]
        status_colors = self.app.pal.get("status_colors", {})
        if status == "Connected":
            color = self.app.pal.get(
                "done", status_colors.get("auto", (self.app.pal["muted"],))[0],
            )
        elif status == "Needs a key" or status == "Not checked":
            color = self.app.pal["muted"]
        elif status == "Testing...":
            color = self.app.pal.get(
                "warning",
                status_colors.get("review", (self.app.pal["muted"],))[0],
            )
        else:
            color = status_colors.get(
                "error", (self.app.pal["muted"],),
            )[0]
        self._provider_status_labels[name].configure(foreground=color)

    def _provider_toggled(self, name):
        settings = self.app.provider_settings
        value = self._provider_enabled[name].get()
        if value == (name in settings.enabled):
            return
        self.app.set_provider_settings(settings.with_enabled(name, value))

    def _move_provider(self, name, step):
        settings = self.app.provider_settings
        order = list(settings.order)
        index = order.index(name)
        target = index + step
        if not 0 <= target < len(order):
            return
        order[index], order[target] = order[target], order[index]
        self.app.set_provider_settings(settings.with_order(order))
        self._layout_providers()

    def _provider_key_edited(self, field):
        key = self._provider_key_vars[field].get().strip()
        self._set_provider_status(
            field, "Needs a key" if not key else "Not checked",
        )

    def _commit_provider_keys(self, name=None):
        settings = self.app.provider_settings
        fields = (
            self._provider_key_vars.items()
            if name is None
            else (
                (field, var)
                for field, var in self._provider_key_vars.items()
                if field == name or field.startswith(f"{name}_")
            )
        )
        for field, variable in fields:
            settings = settings.with_key(field, variable.get())
        if settings != self.app.provider_settings:
            self.app.set_provider_settings(settings)
        if name is not None:
            key = self._provider_key_vars[name].get().strip()
            self._set_provider_status(
                name, "Needs a key" if not key else "Not checked",
            )

    def _toggle_provider_key_visibility(self, field):
        entry = self._provider_key_entries[field]
        show = not bool(entry.cget("show") == "")
        entry.configure(show="" if show else "•")
        self._provider_key_eyes[field].itemconfigure(
            "slash", state="normal" if show else "hidden",
        )

    def _test_provider(self, name):
        if name in _KEY_FIELDS:
            self._commit_provider_keys(name)
        self._set_provider_status(name, "Testing...")
        settings = self.app.provider_settings
        tmdb_key = self.vars["key"].get().strip()
        self._executor.submit(
            self._provider_test_job, name, settings, tmdb_key,
        )

    def _provider_test_job(self, name, settings, tmdb_key):
        try:
            result = test_provider(name, settings, tmdb_key)
        except Exception as error:  # never leave the label on "Testing..."
            result = str(error)[:80] or "Failed"
        if not self._closed:
            self._queue.put(("provider_status", name, result))
