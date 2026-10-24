import tkinter as tk
from tkinter import ttk
import webbrowser

from ...core.providers.router import test_provider
from ...core.providers.settings import PROVIDER_INFO

_KEY_FIELDS = {
    "omdb": (("omdb", "API key"),),
}
_COMPACT_PROVIDERS = {"tmdb", "tvmaze", "anilist", "omdb", "offline"}
_SOURCE_NAMES = {
    "tmdb": "TMDb",
    "tvmaze": "TVmaze",
    "anilist": "AniList",
    "omdb": "OMDb",
    "offline": "Offline Guess",
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
        self._provider_priority_rows = {}
        self._provider_move_buttons = {}
        self._provider_card_surfaces = []
        self._provider_card_titles = []
        self._provider_card_labels = []
        priority_group = self._group(frame, "Priority List")
        priority_group.columnconfigure(0, weight=1)
        for name in settings.order:
            row = tk.Frame(
                priority_group, background=self.app.pal["preset_surface"],
                padx=8, pady=5,
            )
            row.columnconfigure(1, weight=1)
            number = tk.Label(
                row, anchor="center", width=2,
                background=self.app.pal["preset_selected"],
                foreground=self.app.pal["muted"], font="TkTextFont",
                padx=5, pady=2,
            )
            number.grid(row=0, column=0, sticky="w", padx=(0, 8))
            label = tk.Label(
                row, anchor="w", background=self.app.pal["preset_surface"],
                foreground=self.app.pal["fg"], font="TkTextFont",
            )
            label.grid(row=0, column=1, sticky="ew")
            controls = ttk.Frame(row)
            controls.grid(row=0, column=2, sticky="e", padx=(8, 0))
            move_up = ttk.Button(
                controls, text="↑", width=3, style="Settings.TButton",
                command=lambda provider=name: self._move_provider(provider, -1),
            )
            move_up.pack(side="left", padx=(0, 4))
            move_down = ttk.Button(
                controls, text="↓", width=3, style="Settings.TButton",
                command=lambda provider=name: self._move_provider(provider, 1),
            )
            move_down.pack(side="left")
            self._provider_priority_rows[name] = (row, number, label)
            self._provider_move_buttons[name] = (move_up, move_down)

        self._label(frame, "Provider Details", font="TkHeadingFont").pack(
            anchor="w", pady=(18, 10),
        )
        self._provider_blocks = {}
        self._provider_enabled = {}
        self._provider_key_vars = {}
        self._provider_key_entries = {}
        self._provider_key_eyes = {}
        self._provider_status = {}
        self._provider_status_labels = {}

        for name in settings.order:
            self._build_provider_group(frame, name, settings)

        self._layout_providers()

    def _build_provider_group(self, frame, name, settings):
        source = _SOURCE_NAMES[name]
        info = PROVIDER_INFO[name]
        compact = name in _COMPACT_PROVIDERS
        surface = self.app.pal["preset_surface"]
        card = tk.Frame(
            frame, background=surface, highlightthickness=1,
            highlightbackground=self.app.pal["preset_border"],
            highlightcolor=self.app.pal["preset_border"],
            padx=12, pady=6 if compact else 10,
        )
        card.pack(fill="x", pady=(0, 14))
        group = tk.Frame(card, background=surface)
        group.columnconfigure(0, weight=1)
        group.columnconfigure(1, weight=0)
        group.columnconfigure(2, weight=0)
        title = tk.Label(
            group if compact else card, text=source, font="TkHeadingFont",
            background=surface, foreground=self.app.pal["fg"],
        )
        if compact:
            title.grid(row=0, column=0, sticky="w")
        else:
            title.pack(anchor="w", pady=(0, 8))
        group.pack(fill="x")
        self._provider_blocks[name] = card
        self._provider_card_surfaces.extend((card, group))
        self._provider_card_titles.append(title)

        enabled = tk.BooleanVar(value=name in settings.enabled)
        enabled.trace_add(
            "write", lambda *_args, provider=name: (
                self._provider_toggled(provider)
            ),
        )
        self._provider_enabled[name] = enabled

        switch_style = (
            "Moviq.Formatting.Switch.TCheckbutton"
            if self.app.have_sv else "Formatting.TCheckbutton"
        )
        self._switch(
            group, enabled, style=switch_style,
        ).grid(
            row=0, column=2, sticky="e",
            pady=2 if compact else 7,
        )
        row = 1
        if name == "offline":
            description = tk.Frame(group, background=surface)
            description.grid(
                row=row, column=0, columnspan=2, sticky="w",
                padx=(0, 18), pady=(0, 3),
            )
            self._provider_label(
                description,
                "Guess from the file name when nothing else can help. "
                "Always sent to Review.",
                muted=True,
            ).pack(side="left")
            row += 1

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
            controls = tk.Frame(group, background=surface)
            controls.grid(row=row, column=1, sticky="e", pady=5)
            self._add_provider_status(
                group, name, 0,
                "Needs a key" if not key_var.get().strip() else "Not checked",
                column=1, padx=(0, 12),
            )
            wrap, entry = self.make_styled_entry(
                controls, key_var, width=30, show="•",
            )
            entry.grid_configure(padx=7, pady=3)
            self._add_provider_key_placeholder(wrap, entry, key_var)
            self._provider_key_entries[key_field] = entry
            eye = self._provider_eye_toggle(wrap, key_field)
            self._provider_key_eyes[key_field] = eye
            wrap.pack(side="left")
            ttk.Button(
                controls, text="Test", style="Settings.TButton",
                command=lambda provider=name: self._test_provider(provider),
            ).pack(side="left", padx=(6, 0))
            if name == "omdb":
                help_row = tk.Frame(group, background=surface)
                self._provider_card_surfaces.append(help_row)
                help_row.grid(
                    row=row, column=0, sticky="w",
                    padx=(0, 18), pady=3,
                )
                self._provider_label(
                    help_row, "Request a free API key from ", muted=True,
                ).pack(side="left")
                self._provider_link(
                    help_row, "OMDb", info["key_url"],
                ).pack(side="left")
                self._provider_label(
                    help_row,
                    " \u2192 check your email \u2192 paste it here.",
                    muted=True,
                ).pack(side="left")

            return

        if name in ("tvmaze", "anilist"):
            self._provider_label(
                group,
                (
                    "Free tv show and episode database. No key needed."
                    if name == "tvmaze"
                    else "Free anime and manga database. No key needed."
                ),
                muted=True,
            ).grid(row=row, column=0, sticky="w", padx=(0, 18))
            controls = tk.Frame(group, background=surface)
            controls.grid(row=row, column=1, sticky="e", pady=2 if compact else 5)
            self._add_provider_status(
                group, name, 0, "Not checked",
                column=1, padx=(0, 12),
            )
            ttk.Button(
                controls, text="Test", style="Settings.TButton",
                command=lambda provider=name: self._test_provider(provider),
            ).pack(side="left", padx=(6, 0))

    def _build_tmdb_key(self, group, row):
        self.key_status_var = tk.StringVar(value="Not checked")
        self.key_status_label = self._provider_label(
            group, textvariable=self.key_status_var, muted=True,
        )
        self.key_status_label.grid(
            row=0, column=1, sticky="e", padx=(0, 12),
        )
        self.key_entry_var = tk.StringVar(value=self.vars["key"].get())
        key_row = tk.Frame(group, background=self.app.pal["preset_surface"])
        key_row.grid(row=row, column=1, sticky="e", pady=5)
        self.key_entry_wrap, self.key_entry = self.make_styled_entry(
            key_row, self.key_entry_var, width=30, show="•",
        )
        self.key_entry.grid_configure(padx=7, pady=3)
        self._add_provider_key_placeholder(
            self.key_entry_wrap, self.key_entry, self.key_entry_var,
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
        self.key_help_text = self._build_api_key_help(group)
        self._set_key_status("Not checked")
        self.key_help_text.grid(
            row=row, column=0, sticky="ew",
            padx=(0, 18), pady=3,
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

    def _add_provider_status(
        self, group, name, row, status, column=1, pady=(0, 7),
        padx=(0, 0),
    ):
        variable = tk.StringVar()
        label = self._provider_label(
            group, textvariable=variable, muted=True,
        )
        label.grid(
            row=row, column=column, sticky="e", pady=pady, padx=padx,
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

    def _add_provider_key_placeholder(self, wrap, entry, variable):
        placeholder = self._provider_label(
            wrap, "Enter API key here", muted=True, cursor="xterm",
        )

        def update_placeholder(*_args):
            if variable.get() or entry.focus_get() is entry:
                placeholder.place_forget()
            else:
                placeholder.place(
                    relx=0, rely=0.5, anchor="w", x=10,
                )

        placeholder.bind(
            "<Button-1>", lambda _event: entry.focus_set(),
        )
        entry.bind(
            "<FocusIn>", lambda _event: placeholder.place_forget(), add="+",
        )
        entry.bind(
            "<FocusOut>", lambda _event: entry.after_idle(update_placeholder),
            add="+",
        )
        variable.trace_add("write", update_placeholder)
        update_placeholder()

    def _provider_label(self, parent, text="", muted=False, accent=False, **kwargs):
        palette = self.app.pal
        foreground = (
            palette["accent"] if accent else
            palette["muted"] if muted else palette["fg"]
        )
        label = tk.Label(
            parent, text=text, font=kwargs.pop("font", "TkTextFont"),
            background=palette["preset_surface"],
            foreground=foreground, bd=0, highlightthickness=0, **kwargs,
        )
        self._provider_card_labels.append((label, muted, accent))
        return label

    def _provider_link(self, parent, text, url):
        link = self._provider_label(parent, text, accent=True, cursor="hand2")
        link.bind(
            "<Button-1>", lambda _event, target=url: webbrowser.open(target),
        )
        return link

    def _layout_providers(self):
        settings = self.app.provider_settings
        self._provider_detail_order = [
            name for name in settings.order if name != "tvmaze"
        ]
        if "tvmaze" in settings.order:
            insert_at = (
                self._provider_detail_order.index("omdb") + 1
                if "omdb" in self._provider_detail_order
                else len(self._provider_detail_order)
            )
            self._provider_detail_order.insert(insert_at, "tvmaze")
        for row, _number, _label in self._provider_priority_rows.values():
            row.pack_forget()
        for group in self._provider_blocks.values():
            group.pack_forget()
        for position, name in enumerate(settings.order):
            priority_row, number, label = self._provider_priority_rows[name]
            priority_row.pack(fill="x", pady=(0, 5))
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
            number.configure(text=str(position + 1))
            label.configure(text=_SOURCE_NAMES[name])
        for name in self._provider_detail_order:
            self._provider_blocks[name].pack(
                fill="x",
                pady=(0, 8) if name in _COMPACT_PROVIDERS else (0, 14),
            )

    def _set_provider_status(self, name, status):
        self._provider_status[name].set(status)

    def _style_provider_status(self, name):
        status = self._provider_status[name].get()
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
        settings = self.app.provider_settings.with_key(field, key)
        if settings != self.app.provider_settings:
            self.app.set_provider_settings(settings)
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
