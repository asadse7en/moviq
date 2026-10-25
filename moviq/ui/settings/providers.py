import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
import webbrowser

from ...core.providers.router import test_provider
from ...core.providers.settings import PROVIDER_INFO

_KEY_FIELDS = {
    "omdb": (("omdb", "API key"),),
}
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
        self._provider_order = list(settings.order)
        self._provider_drag_name = None
        self._provider_rows = {}
        self._provider_card_surfaces = []
        self._provider_card_titles = []
        self._provider_card_labels = []
        self._provider_descriptions = []
        self._provider_titles = {}
        self._provider_test_buttons = {}
        self._provider_blocks = {}
        self._provider_enabled = {}
        self._provider_key_vars = {}
        self._provider_key_entries = {}
        self._provider_key_eyes = {}
        self._provider_key_wraps = {}
        self._provider_status = {}
        self._provider_status_labels = {}
        self._provider_status_dots = {}
        self._provider_key_check_after = {}
        self._provider_test_generation = {}

        for name in self._provider_order:
            self._build_provider_group(frame, name, settings)

        self._refresh_provider_ranks()
        self._layout_providers()
        self.after_idle(self._refresh_provider_ranks)
        for name in self._provider_order:
            if name != "tmdb" and name in settings.enabled:
                self._test_provider(name)

    def _build_provider_group(self, frame, name, settings):
        info = PROVIDER_INFO[name]
        surface = self.app.pal["preset_surface"]
        card = tk.Frame(
            frame, background=surface, highlightthickness=1,
            highlightbackground=self.app.pal["preset_border"],
            highlightcolor=self.app.pal["preset_border"],
            padx=9, pady=0, height=68,
        )
        card.grid_propagate(False)
        card.columnconfigure(2, weight=1)
        card.columnconfigure(3, minsize=180)
        card.columnconfigure(4, minsize=65)
        card.columnconfigure(5, minsize=160)
        card.columnconfigure(6, minsize=45)
        self._provider_blocks[name] = card
        self._provider_card_surfaces.append(card)
        self._provider_rows[name] = {"card": card, "surfaces": [card]}

        handle = tk.Canvas(
            card, width=20, height=32, background=surface,
            highlightthickness=0,
            cursor="fleur" if name != "offline" else "",
            takefocus=0,
        )
        self._provider_card_surfaces.append(handle)
        self._provider_rows[name]["surfaces"].append(handle)
        for y in (9, 15, 21):
            handle.create_line(
                5, y, 15, y,
                fill=(
                    self.app.pal["preset_border"]
                    if name == "offline" else self.app.pal["muted"]
                ),
                width=2,
                tags="grip",
            )
        handle.grid(row=0, column=0, padx=(0, 5), sticky="w")
        if name != "offline":
            handle.bind(
                "<ButtonPress-1>",
                lambda event, provider=name: self._provider_drag_start(
                    provider, event,
                ),
            )
            handle.bind(
                "<B1-Motion>",
                lambda event, provider=name: self._provider_drag_motion(
                    provider, event,
                ),
            )
            handle.bind(
                "<ButtonRelease-1>",
                lambda event, provider=name: self._provider_drag_end(
                    provider, event,
                ),
            )

        number = tk.Label(
            card, text=str(self._provider_order.index(name) + 1),
            anchor="center", width=2,
            background=self.app.pal["preset_selected"],
            foreground=self.app.pal["muted"], font="TkTextFont",
            padx=5, pady=2,
        )
        number.grid(row=0, column=1, sticky="w", padx=(0, 10))
        self._provider_rows[name].update(handle=handle, number=number)
        identity = tk.Frame(card, background=surface)
        self._provider_card_surfaces.append(identity)
        self._provider_rows[name]["surfaces"].append(identity)
        identity.grid(row=0, column=2, sticky="ew", padx=(0, 10))
        identity.columnconfigure(0, weight=1, minsize=0)
        title = tk.Label(
            identity, text=_SOURCE_NAMES[name], font="TkHeadingFont",
            background=surface, foreground=self.app.pal["fg"],
        )
        title.grid(row=0, column=0, sticky="w")
        description_links = info.get("description_links")
        if description_links is None and info.get("description_link"):
            description_links = (info["description_link"],)
        description = tk.Text(
            identity, width=1, height=1, wrap="none",
            font="TkTextFont", background=surface,
            foreground=self.app.pal["muted"], relief="flat",
            borderwidth=0, highlightthickness=0, padx=0, pady=0,
            takefocus=False, exportselection=False,
        )
        description.bind(
            "<Configure>",
            lambda _event, widget=description, provider_info=info: (
                self._render_provider_description(widget, provider_info)
            ),
        )
        description.configure(state="disabled", cursor="")
        description.grid(
            row=1, column=0, sticky="ew", pady=(2, 0),
        )
        self._provider_card_surfaces.append(description)
        self._provider_rows[name]["surfaces"].append(description)
        self._provider_descriptions.append((description, info))
        self._provider_card_titles.append(title)
        self._provider_titles[name] = title

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
        self._switch(card, enabled, style=switch_style).grid(
            row=0, column=6, sticky="e", padx=(8, 0),
        )
        if name == "tmdb":
            key_var = tk.StringVar(value=self.vars["key"].get())
            self.key_entry_var = key_var
            self._build_provider_key_control(card, name, "key", key_var)
            self.key_entry_wrap = self._provider_key_wraps["key"]
            self.key_entry = self._provider_key_entries["key"]
            self.key_eye_canvas = self._provider_key_eyes["key"]
            self.key_entry.bind("<<Copy>>", self._copy_api_key, add="+")
            self.key_entry.bind("<Control-z>", self._undo_key_entry)
            self.key_entry.bind("<Control-y>", self._redo_key_entry)
            self.key_status_var = tk.StringVar(value="Not checked")
            self._provider_status[name] = self.key_status_var
            test_command = self.test_key
        elif name in _KEY_FIELDS:
            key_field = _KEY_FIELDS[name][0][0]
            key_var = tk.StringVar(value=settings.key(key_field))
            self._provider_key_vars[key_field] = key_var
            key_var.trace_add(
                "write",
                lambda *_args, field=key_field: (
                    self._provider_key_edited(field)
                ),
            )
            self._build_provider_key_control(card, name, key_field, key_var)
            self._provider_status[name] = tk.StringVar(
                value=(
                    "Disabled" if name not in settings.enabled else (
                        "Needs a key"
                        if not key_var.get().strip() else "Not checked"
                    )
                ),
            )
            test_command = lambda provider=name: self._test_provider(provider)
        else:
            status = (
                ("Connected" if name in settings.enabled else "Disabled")
                if name == "offline" else
                "Not checked" if name in settings.enabled else "Disabled"
            )
            self._provider_status[name] = tk.StringVar(value=status)
            test_command = lambda provider=name: self._test_provider(provider)

        self._add_provider_status(
            card, name, self._provider_status[name].get(),
        )
        self._provider_test_buttons[name] = ttk.Button(
            card, text="Test", style="Settings.TButton",
            command=test_command,
        )
        self._provider_test_buttons[name].grid(
            row=0, column=4, padx=(6, 0),
        )
        self._set_provider_row_enabled(
            name, name in settings.enabled,
        )

    def _build_provider_key_control(self, row, name, field, variable):
        self._provider_key_vars[field] = variable
        wrap, entry = self.make_styled_entry(
            row, variable, width=19, show="•",
        )
        entry.grid_configure(padx=7, pady=6)
        self._add_provider_key_placeholder(wrap, entry, variable)
        self._provider_key_entries[field] = entry
        self._provider_key_wraps[field] = wrap
        eye = self._provider_eye_toggle(wrap, field)
        self._provider_key_eyes[field] = eye
        wrap.grid(row=0, column=3, sticky="ew")

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

    def _render_provider_description(self, widget, info):
        text = info["description"]
        links = info.get("description_links")
        if links is None and info.get("description_link"):
            links = (info["description_link"],)
        links = tuple(links or ())
        available = max(0, widget.winfo_width() - 2)
        font = tkfont.nametofont(widget.cget("font"), root=widget)
        visible_length = len(text)
        if font.measure(text) > available:
            visible_length = 0
            for length in range(len(text), -1, -1):
                candidate = text[:length].rstrip() + "\u2026"
                if font.measure(candidate) <= available:
                    visible_length = length
                    break
            for link in links:
                link_start = text.find(link["text"])
                link_end = link_start + len(link["text"])
                if link_start >= 0 and link_start < visible_length < link_end:
                    visible_length = link_start
            text = text[:visible_length].rstrip() + "\u2026"

        widget.configure(state="normal")
        widget.delete("1.0", "end")
        content = text[:-1] if text.endswith("\u2026") else text
        spans = []
        for index, link in enumerate(links):
            start = content.find(link["text"])
            end = start + len(link["text"])
            if start >= 0 and end <= len(content):
                spans.append((start, end, index, link["url"]))

        cursor = 0
        for start, end, index, url in spans:
            widget.insert("end", content[cursor:start])
            tag = f"description_link_{index}"
            widget.tag_configure(
                tag, foreground=self.app.pal["accent"],
            )
            widget.tag_bind(
                tag, "<Button-1>",
                lambda _event, target=url: webbrowser.open(target),
            )
            widget.tag_bind(
                tag, "<Enter>",
                lambda _event, target=widget: target.configure(cursor="hand2"),
            )
            widget.tag_bind(
                tag, "<Leave>",
                lambda _event, target=widget: target.configure(cursor=""),
            )
            widget.insert("end", content[start:end], tag)
            cursor = end
        widget.insert("end", content[cursor:] + text[len(content):])
        widget.configure(state="disabled")

    def _add_provider_status(self, card, name, status):
        variable = self._provider_status[name]
        status_row = tk.Frame(card, background=self.app.pal["preset_surface"])
        self._provider_card_surfaces.append(status_row)
        self._provider_rows[name]["surfaces"].append(status_row)
        status_row.grid(row=0, column=5, sticky="w", padx=(8, 0))
        dot = tk.Canvas(
            status_row, width=10, height=10,
            background=self.app.pal["preset_surface"],
            highlightthickness=0,
        )
        dot.grid(row=0, column=0, padx=(0, 6))
        dot.create_oval(1, 1, 9, 9, tags="status")
        label = self._provider_label(
            status_row, textvariable=variable, muted=True, width=18,
            anchor="w",
        )
        label.grid(row=0, column=1, sticky="w")
        self._provider_status_labels[name] = label
        self._provider_status_dots[name] = dot
        variable.trace_add(
            "write",
            lambda *_args, provider=name: (
                self._style_provider_status(provider)
            ),
        )
        self._set_provider_status(name, status)
        if name == "tmdb":
            self.key_status_label = label

    def _add_provider_key_placeholder(self, wrap, entry, variable):
        placeholder = self._provider_label(
            wrap, "API key", muted=True, cursor="xterm",
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

    def _layout_providers(self):
        self._refresh_provider_ranks()
        for position, name in enumerate(self._provider_order, start=1):
            row = self._provider_rows[name]
            card = row["card"]
            card.configure(
                highlightbackground=(
                    self.app.pal["accent"]
                    if name == self._provider_drag_name
                    else self.app.pal["preset_border"]
                ),
                highlightcolor=(
                    self.app.pal["accent"]
                    if name == self._provider_drag_name
                    else self.app.pal["preset_border"]
                ),
            )

        next_card = None
        for name in reversed(self._provider_order):
            card = self._provider_rows[name]["card"]
            pack_options = {"fill": "x", "pady": (0, 6)}
            if card.winfo_manager() == "pack":
                if next_card is not None:
                    card.pack_configure(before=next_card, **pack_options)
                else:
                    card.pack_configure(**pack_options)
            elif next_card is not None:
                card.pack(before=next_card, **pack_options)
            else:
                card.pack(**pack_options)
            next_card = card

    def _refresh_provider_ranks(self):
        for position, name in enumerate(self._provider_order, start=1):
            number = self._provider_rows[name]["number"]
            enabled = self._provider_enabled[name].get()
            number.configure(
                text=str(position),
                background=(
                    self.app.pal["preset_selected"] if enabled
                    else self.app.pal["preset_border"]
                ),
                foreground=self.app.pal["muted"],
            )

    def _provider_drag_start(self, name, _event):
        if name == "offline":
            return "break"
        self._provider_drag_name = name
        self._provider_drag_original = tuple(self._provider_order)
        self._layout_providers()
        return "break"

    def _provider_drag_motion(self, name, event):
        if name != self._provider_drag_name:
            return "break"
        self.update_idletasks()
        movable = [
            provider for provider in self._provider_order
            if provider not in (name, "offline")
        ]
        target = len(movable)
        for index, provider in enumerate(movable):
            card = self._provider_blocks[provider]
            midpoint = card.winfo_rooty() + card.winfo_height() / 2
            if event.y_root < midpoint:
                target = index
                break
        movable.insert(target, name)
        new_order = movable + ["offline"]
        if new_order != self._provider_order:
            self._provider_order = new_order
            self._layout_providers()
        return "break"

    def _provider_drag_end(self, name, _event):
        if name != self._provider_drag_name:
            return "break"
        self._provider_drag_name = None
        self._layout_providers()
        if tuple(self._provider_order) != self._provider_drag_original:
            self.app.set_provider_settings(
                self.app.provider_settings.with_order(self._provider_order),
            )
        self._provider_drag_original = None
        return "break"

    def _set_provider_status(self, name, status):
        self._provider_status[name].set(status)

    def _style_provider_status(self, name):
        status = self._provider_status[name].get()
        status_colors = self.app.pal.get("status_colors", {})
        if status == "Connected":
            color = self.app.pal.get(
                "done", status_colors.get("auto", (self.app.pal["muted"],))[0],
            )
        elif status in ("Disabled", "Needs a key", "Not checked"):
            color = self.app.pal["muted"]
        elif status in ("Testing...", "Checking", "Wrong format"):
            color = self.app.pal.get(
                "warning",
                status_colors.get("review", (self.app.pal["muted"],))[0],
            )
        else:
            color = status_colors.get(
                "error", (self.app.pal["muted"],),
            )[0]
        self._provider_status_labels[name].configure(foreground=color)
        self._provider_status_dots[name].itemconfigure(
            "status", fill=color, outline=color,
        )

    def _provider_toggled(self, name):
        settings = self.app.provider_settings
        value = self._provider_enabled[name].get()
        if value == (name in settings.enabled):
            return
        self.app.set_provider_settings(settings.with_enabled(name, value))
        self._set_provider_row_enabled(name, value)
        if not value:
            self._provider_test_generation[name] = (
                self._provider_test_generation.get(name, 0) + 1
            )
            self._set_provider_status(name, "Disabled")
            if name == "tmdb":
                self._key_generation += 1
                if self._key_after is not None:
                    self.after_cancel(self._key_after)
                    self._key_after = None
                if self._saved_key_status_after is not None:
                    self.after_cancel(self._saved_key_status_after)
                    self._saved_key_status_after = None
                    self._saved_key_validation_status = None
            return
        if name == "tmdb":
            self.test_key()
        else:
            self._test_provider(name)

    def _set_provider_row_enabled(self, name, enabled):
        background = self.app.pal["preset_surface"]
        for surface in self._provider_rows[name]["surfaces"]:
            surface.configure(background=background)
        self._provider_status_labels[name].configure(
            background=background,
        )
        self._provider_status_dots[name].configure(
            background=background,
        )
        title = self._provider_titles[name]
        title.configure(
            background=background,
            foreground=(
                self.app.pal["fg"] if enabled
                else self.app.pal["muted"]
            ),
        )
        self._provider_test_buttons[name].configure(
            state="normal" if enabled else "disabled",
        )
        number = self._provider_rows[name]["number"]
        number.configure(
            background=(
                self.app.pal["preset_selected"] if enabled
                else self.app.pal["preset_border"]
            ),
            foreground=self.app.pal["muted"],
        )
        if name == "offline":
            self._provider_rows[name]["handle"].itemconfigure(
                "grip", fill=self.app.pal["preset_border"],
            )

    def _provider_key_edited(self, field):
        key = self._provider_key_vars[field].get().strip()
        settings = self.app.provider_settings.with_key(field, key)
        if settings != self.app.provider_settings:
            self.app.set_provider_settings(settings)
        after_id = self._provider_key_check_after.pop(field, None)
        if after_id is not None:
            self.after_cancel(after_id)
        generation = self._provider_test_generation.get(field, 0) + 1
        self._provider_test_generation[field] = generation
        self._set_provider_status(
            field, "Needs a key" if not key else "Not checked",
        )
        if key:
            if field not in self.app.provider_settings.enabled:
                self._set_provider_status(field, "Disabled")
                return
            self._provider_key_check_after[field] = self.after(
                800,
                lambda provider=field, token=generation: (
                    self._run_provider_key_check(provider, token)
                ),
            )

    def _run_provider_key_check(self, name, generation):
        if generation != self._provider_test_generation.get(name):
            return
        self._provider_key_check_after.pop(name, None)
        self._test_provider(name)

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
        if name not in self.app.provider_settings.enabled:
            self._set_provider_status(name, "Disabled")
            return
        after_id = self._provider_key_check_after.pop(name, None)
        if after_id is not None:
            self.after_cancel(after_id)
        if name in _KEY_FIELDS:
            self._commit_provider_keys(name)
        generation = self._provider_test_generation.get(name, 0) + 1
        self._provider_test_generation[name] = generation
        self._set_provider_status(name, "Testing...")
        settings = self.app.provider_settings
        tmdb_key = self.vars["key"].get().strip()
        self._executor.submit(
            self._provider_test_job, name, settings, tmdb_key, generation,
        )

    def _provider_test_job(self, name, settings, tmdb_key, generation):
        try:
            result = test_provider(name, settings, tmdb_key)
        except Exception as error:  # never leave the label on "Testing..."
            result = str(error)[:80] or "Failed"
        if not self._closed:
            self._queue.put((
                "provider_status", name, generation, result,
            ))
