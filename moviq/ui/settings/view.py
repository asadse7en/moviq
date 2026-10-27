import queue
import re
import tkinter as tk
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from ...core.tmdb import MAX_WORKERS
from ..controls import RoundedCard, draw_rounded_rectangle
from ..icons import draw_icon
from ._common import APP_VERSION
from .about import AboutTabMixin
from .formatting import FormattingTabMixin
from .providers import ProvidersTabMixin
from .support import SettingsSupportMixin


class SidebarNavItem(tk.Canvas):
    def __init__(self, master, title, command, palette):
        super().__init__(
            master, height=40, highlightthickness=0, bd=0,
            background=palette["header_bg"], cursor="hand2",
        )
        self.title = title
        self.command = command
        self.palette = palette
        self.selected = False
        self.hovered = False
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def set_active(self, selected):
        self.selected = selected
        self._draw()

    def set_palette(self, palette):
        self.palette = palette
        self.configure(background=palette["header_bg"])
        self._draw()

    def _on_enter(self, _event):
        self.hovered = True
        self._draw()

    def _on_leave(self, _event):
        self.hovered = False
        self._draw()

    def _on_click(self, _event):
        self.command()
        return "break"

    def _draw(self, _event=None):
        if not self.winfo_exists():
            return
        palette = self.palette
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        background = (
            palette["logo_bg"] if self.selected else
            palette["header_btn_hi"] if self.hovered else
            palette["header_bg"]
        )
        foreground = (
            palette["header_accent"] if self.selected else
            palette["fg"] if self.hovered else
            palette["header_muted"]
        )
        self.delete("all")
        draw_rounded_rectangle(
            self, 0, 0, width, height, 8,
            background, "", background=palette["header_bg"],
        )
        if self.selected:
            draw_rounded_rectangle(
                self, 0, 8, 3, height - 8, 1,
                palette["header_accent"], "",
                background=background,
            )
        self.create_text(
            20, height / 2, text=self.title, anchor="w",
            fill=foreground, font=("TkDefaultFont", 11),
        )


class SidebarBackAction(tk.Canvas):
    def __init__(self, master, command, palette):
        self.command = command
        self.palette = palette
        self.hovered = False
        super().__init__(
            master, height=36, highlightthickness=0, bd=0,
            background=palette["header_bg"],
            cursor="hand2",
        )
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def set_palette(self, palette):
        self.palette = palette
        self._draw()

    def _on_enter(self, _event):
        self.hovered = True
        self._draw()

    def _on_leave(self, _event):
        self.hovered = False
        self._draw()

    def _on_click(self, _event):
        self.command()
        return "break"

    def _draw(self, _event=None):
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        foreground = (
            self.palette["fg"] if self.hovered
            else self.palette["header_muted"]
        )
        self.configure(background=self.palette["header_bg"])
        self.delete("all")
        draw_icon(
            self, "undo-2", 16, foreground, (19, height / 2),
        )
        self.create_text(
            38, height / 2, text="Back", anchor="w",
            fill=foreground, font=("TkTextFont", 10),
        )


class SettingsView(
    SettingsSupportMixin, FormattingTabMixin, ProvidersTabMixin,
    AboutTabMixin, ttk.Frame,
):
    """Tabbed settings layout and asynchronous validation/update status."""

    _SECTIONS = (
        ("Sources", "Sources"),
        ("Formatting", "Formatting"),
        ("Files & Safety", "Files & Safety"),
        ("About", "About"),
    )
    _V3_KEY = re.compile(r"^[0-9a-fA-F]{32}$")

    def __init__(
        self, parent, app, store, variables,
    ):
        super().__init__(parent)
        self.app = app
        self.store = store
        self.vars = variables
        self._queue = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        self._closed = False
        self._key_after = None
        self._key_generation = 0
        self._key_history = []
        self._key_redo_history = []
        self._key_history_value = None
        self._key_history_suspended = False
        self._saved_key_status_after = None
        self._saved_key_validation_status = None
        self._update_checking = False
        self._poll_id = None
        self._labels = []
        self._settings_cards = []
        self._settings_card_surfaces = []
        self._preset_cards = {"movie": [], "tv": []}
        self._format_preset_grids = {}
        self._format_grid_columns = {}
        self._syncing_tv_folder = False
        self._styled_entries = []
        self._current_section = "Sources"
        self._wheel_remainder = 0.0
        self._scroll_bindings = []
        self.store.on_error = lambda error: self._queue.put(
            ("save_error", str(error)),
        )
        self._build()
        self._bind_preferences()
        self._show_section("Sources")
        self._schedule_key_check(800)
        self._poll_id = self.after(100, self._poll)

    def _build(self):
        self._configure_settings_styles()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self.sidebar = ttk.Frame(self, padding=(10, 18, 10, 15))
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar.configure(style="Settings.Sidebar.TFrame", width=220)
        self.sidebar.pack_propagate(False)
        self.content = ttk.Frame(self, padding=(26, 20))
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(0, weight=1)
        self.content_canvas = tk.Canvas(
            self.content, background=self.app.pal["bg"],
            highlightthickness=0, bd=0, takefocus=True,
        )
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll_content = ttk.Frame(self.content_canvas)
        self._scroll_window = self.content_canvas.create_window(
            0, 0, anchor="nw", window=self.scroll_content,
        )
        self.scroll_content.columnconfigure(0, weight=1)
        self.content_canvas.bind(
            "<Configure>", self._resize_scroll_content,
        )
        self.scroll_content.bind(
            "<Configure>", self._update_scroll_region,
        )
        self._bind_scroll_events()
        self.sidebar_brand = ttk.Frame(
            self.sidebar, style="Settings.Sidebar.TFrame",
        )
        self.sidebar_brand.pack(fill="x", pady=(0, 30))
        self.sidebar_brand_icon = tk.Canvas(
            self.sidebar_brand, width=32, height=32,
            background=self.app.pal["header_bg"],
            highlightthickness=0, bd=0,
        )
        self.sidebar_brand_icon.pack(side="left", padx=(2, 8))
        self._draw_sidebar_brand()
        ttk.Label(
            self.sidebar_brand, text="Moviq",
            style="Settings.Brand.TLabel",
        ).pack(side="left")
        self.back_action = SidebarBackAction(
            self.sidebar, self.app.leave_settings, self.app.pal,
        )
        self.back_action.pack(fill="x", pady=(0, 24))
        self.nav_buttons = {}
        for key, title in self._SECTIONS:
            button = SidebarNavItem(
                self.sidebar, title,
                lambda name=key: self._show_section(name),
                self.app.pal,
            )
            button.pack(fill="x", pady=3)
            self.nav_buttons[key] = button
        self.section_frames = {}
        for name, _title in self._SECTIONS:
            frame = ttk.Frame(self.scroll_content)
            frame.grid(row=0, column=0, sticky="nsew")
            frame.columnconfigure(0, weight=1)
            self.section_frames[name] = frame
        self._build_providers(self.section_frames["Sources"])
        self._build_formatting(self.section_frames["Formatting"])
        self._build_safety(self.section_frames["Files & Safety"])
        self._build_about(self.section_frames["About"])

    def _label(self, parent, text="", muted=False, **kwargs):
        if "style" not in kwargs:
            current = parent
            while current is not None:
                owner = getattr(current, "master", None)
                if (
                    isinstance(owner, RoundedCard)
                    and owner.content is current
                ):
                    kwargs["style"] = "Settings.Card.TLabel"
                    break
                current = owner
        label = ttk.Label(parent, text=text, **kwargs)
        self._labels.append((label, muted))
        label.configure(foreground=(
            self.app.pal["muted"] if muted else self.app.pal["fg"]
        ))
        return label

    def _section_header(self, parent, title, subtitle, bottom_pad=18):
        self._label(parent, title, font="MoviqTitleFont").pack(
            anchor="w",
        )
        self._label(parent, subtitle, muted=True).pack(
            anchor="w", pady=(3, bottom_pad),
        )

    def _group(self, parent, title):
        palette = self.app.pal
        card = RoundedCard(
            parent, palette["bg"], palette["preset_surface"],
            palette["preset_border"],
        )
        card.pack(fill="x", pady=(0, 14))
        self._settings_cards.append(card)
        frame = card.content
        frame.columnconfigure(1, weight=1)
        self._label(
            frame, title, font="TkHeadingFont",
            style="Settings.Card.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 6))
        return frame

    def _configure_settings_styles(self, palette=None):
        style = ttk.Style(self)
        palette = palette or self.app.pal
        style.configure(
            "Settings.TLabelframe.Label",
            foreground=palette["fg"],
            font=("TkDefaultFont", 11, "bold"),
        )
        style.configure(
            "Settings.Card.TLabel",
            background=palette["preset_surface"],
            foreground=palette["fg"],
        )
        style.map(
            "Settings.Card.TLabel",
            background=[
                ("disabled", palette["preset_surface"]),
                ("active", palette["preset_surface"]),
                ("!disabled", palette["preset_surface"]),
            ],
        )
        style.configure(
            "Settings.Card.TFrame",
            background=palette["preset_surface"],
        )
        style.configure(
            "Settings.Card.TSeparator",
            background=palette["preset_surface"],
        )
        format_switch_style = (
            "Moviq.Formatting.Switch.TCheckbutton" if self.app.have_sv
            else "Formatting.TCheckbutton"
        )
        style.configure(
            format_switch_style,
            background=palette["preset_surface"],
            foreground=palette["fg"],
            padding=0,
        )
        style.map(
            format_switch_style,
            background=[
                ("disabled", palette["preset_surface"]),
                ("active", palette["preset_surface"]),
            ],
            foreground=[
                ("disabled", palette["muted"]),
                ("!disabled", palette["fg"]),
            ],
        )
        safety_switch_style = "Settings.Safety.Switch.TCheckbutton"
        style.configure(
            safety_switch_style,
            background=palette["preset_surface"],
            foreground=palette["fg"],
            padding=0,
        )
        style.map(
            safety_switch_style,
            background=[
                ("disabled", palette["preset_surface"]),
                ("active", palette["preset_surface"]),
            ],
            foreground=[
                ("disabled", palette["muted"]),
                ("!disabled", palette["fg"]),
            ],
        )
        if self.app.have_sv and style.layout("Moviq.Switch.TCheckbutton"):
            style.layout(
                safety_switch_style,
                style.layout("Moviq.Switch.TCheckbutton"),
            )
        style.configure(
            "Settings.Safety.TEntry",
            fieldbackground=palette["preset_surface"],
            foreground=palette["fg"],
            insertcolor=palette["fg"],
            bordercolor=palette["preset_border"],
        )
        style.configure(
            "Settings.TButton",
            background=palette["btn"],
            foreground=palette["fg"],
            borderwidth=0,
            relief="flat",
            padding=(14, 8),
            font="TkDefaultFont",
        )
        style.map(
            "Settings.TButton",
            background=[
                ("pressed", palette["accent"]),
                ("active", palette["btn_hi"]),
                ("disabled", palette["btn"]),
            ],
            foreground=[
                ("disabled", palette["muted"]),
                ("!disabled", palette["fg"]),
            ],
        )
        style.configure(
            "Settings.Sidebar.TFrame",
            background=palette["header_bg"],
        )
        style.configure(
            "Settings.Brand.TLabel",
            background=palette["header_bg"],
            foreground=palette["fg"],
            font=("TkDefaultFont", 13, "bold"),
        )

    def _draw_sidebar_brand(self):
        canvas = self.sidebar_brand_icon
        canvas.configure(background=self.app.pal["header_bg"])
        canvas.delete("all")
        color = self.app.pal["header_accent"]
        background = self.app.pal["header_bg"]
        draw_rounded_rectangle(
            canvas, 3, 3, 29, 29, 5, background, color,
            width=2, background=background,
        )
        draw_rounded_rectangle(
            canvas, 8, 8, 24, 24, 3, background, color,
            background=background,
        )
        canvas.create_line(
            11, 16, 21, 16, fill=color, width=2, capstyle="round",
        )

    def _resize_scroll_content(self, event):
        self.content_canvas.itemconfigure(
            self._scroll_window, width=event.width,
        )

    def _update_scroll_region(self, _event=None):
        bounds = self.content_canvas.bbox("all")
        if bounds is None:
            return
        left, top, right, bottom = bounds
        bottom = max(bottom, top + self.content_canvas.winfo_height())
        self.content_canvas.configure(
            scrollregion=(left, top, right, bottom),
        )

    def _bind_scroll_events(self):
        bindings = (
            ("<MouseWheel>", self._on_mousewheel),
            ("<Button-4>", self._on_mousewheel_linux),
            ("<Button-5>", self._on_mousewheel_linux),
            ("<Prior>", self._on_scroll_key),
            ("<Next>", self._on_scroll_key),
            ("<Control-Home>", self._on_scroll_key),
            ("<Control-End>", self._on_scroll_key),
            ("<Up>", self._on_scroll_key),
            ("<Down>", self._on_scroll_key),
        )
        for sequence, callback in bindings:
            binding_id = self.bind_all(sequence, callback, add="+")
            self._scroll_bindings.append((sequence, binding_id))

    def _pointer_over_content(self):
        x, y = self.winfo_pointerxy()
        left = self.content_canvas.winfo_rootx()
        top = self.content_canvas.winfo_rooty()
        return (
            self.content_canvas.winfo_ismapped()
            and left <= x < left + self.content_canvas.winfo_width()
            and top <= y < top + self.content_canvas.winfo_height()
        )

    def _on_mousewheel(self, event):
        if not self._pointer_over_content():
            return
        self._wheel_remainder += -event.delta / 30
        units = int(self._wheel_remainder)
        if units:
            if not self._scroll_settings_canvas(units):
                self._wheel_remainder = 0.0
                return "break"
            self._wheel_remainder -= units
        return "break"

    def _on_mousewheel_linux(self, event):
        if not self._pointer_over_content():
            return
        units = -1 if event.num == 4 else 1
        self._scroll_settings_canvas(units)
        return "break"

    def _scroll_settings_canvas(self, units, what="units"):
        first, last = self.content_canvas.yview()
        if units < 0 and first <= 0:
            return False
        if units > 0 and last >= 1:
            return False
        self.content_canvas.yview_scroll(units, what)
        return True

    def _on_scroll_key(self, event):
        focus = self.focus_get()
        if not self._is_scroll_content_widget(focus):
            return
        if event.keysym in ("Up", "Down") and isinstance(
            focus, (
                tk.Entry, ttk.Entry, ttk.Combobox, ttk.Scale,
                ttk.Checkbutton, ttk.Radiobutton, tk.Text,
            ),
        ):
            return
        if event.keysym == "Prior":
            self._scroll_settings_canvas(-1, "pages")
        elif event.keysym == "Next":
            self._scroll_settings_canvas(1, "pages")
        elif event.keysym == "Home":
            self.content_canvas.yview_moveto(0)
        elif event.keysym == "End":
            self.content_canvas.yview_moveto(1)
        elif event.keysym == "Up":
            self._scroll_settings_canvas(-1)
        elif event.keysym == "Down":
            self._scroll_settings_canvas(1)
        else:
            return
        return "break"

    def _is_scroll_content_widget(self, widget):
        while widget is not None:
            if widget is self.scroll_content:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _row(self, parent, row, title, control, description="", style=None):
        self._label(
            parent, title, font="TkHeadingFont", style=style,
        ).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=7)
        if description:
            self._label(parent, description, muted=True, style=style).grid(
                row=row + 1, column=0, sticky="w", padx=(0, 18),
                pady=(0, 7),
            )
            control.grid(
                row=row, column=1, rowspan=2, sticky="e", pady=7,
            )
            return row + 2
        control.grid(row=row, column=1, sticky="e", pady=7)
        return row + 1

    def _switch(self, parent, variable, text="", style=None):
        style = style or (
            "Moviq.Switch.TCheckbutton"
            if self.app.have_sv else "TCheckbutton"
        )
        return ttk.Checkbutton(parent, text=text, variable=variable, style=style)

    def make_styled_entry(
        self, parent, textvariable, width=None, show=None,
    ):
        """Create a card-colored entry with a theme-aware focus border."""
        palette = self.app.pal
        wrap = tk.Frame(
            parent,
            background=palette["preset_surface"],
            highlightthickness=1,
            highlightbackground=palette["preset_border"],
            highlightcolor=palette["preset_border"],
        )
        wrap._moviq_focus_control = True
        entry_options = {
            "master": wrap,
            "textvariable": textvariable,
            "font": "TkTextFont",
            "background": palette["preset_surface"],
            "readonlybackground": palette["preset_surface"],
            "foreground": palette["fg"],
            "disabledforeground": palette["muted"],
            "disabledbackground": palette["preset_surface"],
            "insertbackground": palette["fg"],
            "relief": "flat",
            "bd": 0,
            "highlightthickness": 0,
            "takefocus": True,
        }
        if width is not None:
            entry_options["width"] = width
        if show is not None:
            entry_options["show"] = show
        entry = tk.Entry(**entry_options)
        entry.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        wrap.columnconfigure(0, weight=1)
        wrap.bind(
            "<FocusIn>",
            lambda _event, container=wrap: self._style_entry_border(
                container, True,
            ),
        )
        wrap.bind(
            "<FocusOut>",
            lambda _event, container=wrap: self._style_entry_border(
                container, False,
            ),
        )
        entry.bind(
            "<FocusIn>",
            lambda _event, container=wrap: self._style_entry_border(
                container, True,
            ),
        )
        entry.bind(
            "<FocusOut>",
            lambda _event, container=wrap: self._style_entry_border(
                container, False,
            ),
        )
        self._styled_entries.append((wrap, entry))
        return wrap, entry

    def _style_entry_border(self, wrap, focused):
        palette = self.app.pal
        border = palette["accent"] if focused else palette["preset_border"]
        wrap.configure(
            background=palette["preset_surface"],
            highlightbackground=border,
            highlightcolor=border,
        )

    def _recolor_styled_entries(self, palette):
        for wrap, entry in self._styled_entries:
            focused = entry == entry.focus_get()
            border = palette["accent"] if focused else palette["preset_border"]
            wrap.configure(
                background=palette["preset_surface"],
                highlightbackground=border,
                highlightcolor=border,
            )
            entry.configure(
                background=palette["preset_surface"],
                readonlybackground=palette["preset_surface"],
                foreground=palette["fg"],
                disabledforeground=palette["muted"],
                disabledbackground=palette["preset_surface"],
                insertbackground=palette["fg"],
            )
        self.key_eye_canvas.configure(background=palette["preset_surface"])
        self.key_eye_canvas.itemconfigure("eye", fill=palette["muted"])
        self.key_eye_canvas.itemconfigure(
            "pupil", fill=palette["muted"], outline=palette["muted"],
        )
        self.key_eye_canvas.itemconfigure("slash", fill=palette["muted"])

    def _link(self, parent, text, url, style=None):
        link = self._label(
            parent, text, cursor="hand2",
            style=style or "Settings.Card.TLabel",
        )
        link.configure(foreground=self.app.pal["accent"])
        link.bind("<Button-1>", lambda _event, target=url: webbrowser.open(target))
        return link

    def _bind_preferences(self):
        self._preference_traces = []
        self._key_history_value = self.key_entry_var.get()
        key_history_trace = self.key_entry_var.trace_add(
            "write", self._record_key_entry_edit,
        )
        self._preference_traces.append(
            (self.key_entry_var, key_history_trace),
        )
        key_draft_trace = self.key_entry_var.trace_add(
            "write", self._key_draft_changed,
        )
        self._preference_traces.append(
            (self.key_entry_var, key_draft_trace),
        )
        keys = {
            "key": self.vars["key"],
            "threshold": self.vars["threshold"],
            "auto_match": self.vars["auto_match"],
            "confirm_apply": self.vars["confirm_apply"],
            "tag_id": self.vars["tag_id"],
            "rename_subtitles": self.vars["rename_subtitles"],
            "library_mode": self.vars["library_mode"],
            "movies_library": self.vars["movies_library"],
            "tv_library": self.vars["tv_library"],
        }
        for key, variable in keys.items():
            trace_id = variable.trace_add(
                "write",
                lambda *_args, setting=key, var=variable: self._preference_changed(
                    setting, var,
                ),
            )
            self._preference_traces.append((variable, trace_id))
        tv_folder_trace = self.vars["tv_folder"].trace_add(
            "write", lambda *_args: self._legacy_tv_folder_changed(),
        )
        self._preference_traces.append(
            (self.vars["tv_folder"], tv_folder_trace),
        )

    def _legacy_tv_folder_changed(self):
        if self._syncing_tv_folder:
            return
        enabled = self.vars["tv_folder"].get()
        self._updating_format.add("tv")
        try:
            self.vars["tv_create_show_folder"].set(enabled)
            self.vars["tv_create_season_folder"].set(enabled)
        finally:
            self._updating_format.discard("tv")
        self._update_pattern_editing("tv")
        self._formatting_changed("tv")

    def _preference_changed(self, key, variable):
        value = variable.get()
        if key == "key":
            self._key_generation += 1
            self._set_key_status("Not checked")
            self._schedule_key_check(800)
        elif key == "threshold":
            value = min(100, max(50, int(round(float(value) / 5) * 5)))
            if variable.get() != value:
                variable.set(value)
        self.store.set(key, value)

    def _poll(self):
        if self._closed:
            return
        try:
            while True:
                message = self._queue.get_nowait()
                if message[0] == "key":
                    _, generation, key, status = message
                    if (
                        generation == self._key_generation
                        and key == self.key_entry_var.get().strip()
                    ):
                        if self._saved_key_status_after is not None:
                            self._saved_key_validation_status = status
                        else:
                            self._set_key_status(status)
                elif message[0] == "update":
                    _, result, latest, release_url = message
                    self._update_checking = False
                    self.update_button.configure(state="normal")
                    if result == "current":
                        self.update_status_var.set(
                            f"You're up to date (Moviq {APP_VERSION}).",
                        )
                        self._update_release_url = None
                    elif result == "available":
                        self.update_status_var.set(
                            f"Moviq {latest.removeprefix('v')} is available.",
                        )
                        self._update_release_url = release_url
                        self.download_update_button.grid()
                    else:
                        self.update_status_var.set(
                            "Couldn't check for updates. Try again later.",
                        )
                        self._update_release_url = None
                    self.update_status_label.configure(
                        foreground=self.app.pal["muted"],
                    )
                elif message[0] == "provider_status":
                    _, name, generation, text = message
                    if generation == self._provider_test_generation.get(name):
                        self._set_provider_status(
                            name,
                            text if name in self.app.provider_settings.enabled
                            else "Disabled",
                        )
                elif message[0] == "save_error":
                    self._set_key_status(
                        f"Couldn't save settings: {message[1]}",
                    )
        except queue.Empty:
            pass
        if not self._closed:
            self._poll_id = self.after(100, self._poll)

    def _show_section(self, name):
        if name == "Formatting":
            width = self.content_canvas.winfo_width()
            if width > 1:
                self._layout_format_body("movie", width)
                self._layout_format_body("tv", width)
        self._current_section = name
        for frame in self.section_frames.values():
            frame.grid_remove()
        self.section_frames[name].grid()
        self._refresh_sidebar()
        self.content_canvas.yview_moveto(0)
        self.after_idle(self._update_scroll_region)

    def _refresh_sidebar(self):
        self.back_action.set_palette(self.app.pal)
        for name, button in self.nav_buttons.items():
            button.set_palette(self.app.pal)
            button.set_active(name == self._current_section)

    def focus_provider_source(self, name):
        self._show_section("Sources")

        def focus():
            field = "key" if name == "tmdb" else name
            target = getattr(self, "_provider_key_entries", {}).get(field)
            if target is None:
                target = getattr(self, "_provider_test_buttons", {}).get(name)
            if target is not None:
                target.focus_set()

        self.after_idle(focus)

    def recolor(self, palette):
        self._configure_settings_styles(palette)
        self._draw_sidebar_brand()
        self._refresh_sidebar()
        self.content_canvas.configure(background=palette["bg"])
        self.recolor_safety(palette)
        self._set_format_selector_theme(palette)
        self._set_library_selector_theme(palette)
        self._recolor_styled_entries(palette)
        for surface in self._provider_card_surfaces:
            surface.configure(background=palette["preset_surface"])
        for card in self._settings_cards:
            card.set_surface(
                palette["bg"], palette["preset_surface"],
                palette["preset_border"],
            )
        for surface in self._settings_card_surfaces:
            surface.configure(background=palette["preset_surface"])
        for name, row in self._provider_rows.items():
            row["number"].configure(
                background=palette["preset_selected"],
                foreground=palette["muted"],
            )
            row["handle"].itemconfigure(
                "grip",
                fill=(
                    palette["preset_border"] if name == "offline"
                    else palette["muted"]
                ),
            )
        for eye in self._provider_key_eyes.values():
            eye.configure(background=palette["preset_surface"])
            eye.itemconfigure("eye", fill=palette["muted"])
            eye.itemconfigure(
                "pupil", fill=palette["muted"], outline=palette["muted"],
            )
            eye.itemconfigure("slash", fill=palette["muted"])
        for description, info in self._provider_descriptions:
            description.configure(
                background=palette["preset_surface"],
                foreground=palette["muted"],
            )
            self._render_provider_description(description, info)
        for dot in self._provider_status_dots.values():
            dot.configure(background=palette["preset_surface"])
        for card in self._provider_blocks.values():
            border = (
                palette["accent"]
                if card is self._provider_blocks.get(self._provider_drag_name)
                else palette["preset_border"]
            )
            card.set_surface(
                palette["bg"], palette["preset_surface"], border,
            )
        for title in self._provider_card_titles:
            title.configure(
                background=palette["preset_surface"],
                foreground=palette["fg"],
            )
        for label, muted, accent in self._provider_card_labels:
            label.configure(
                background=palette["preset_surface"],
                foreground=(
                    palette["accent"] if accent else
                    palette["muted"] if muted else palette["fg"]
                ),
            )
        for label, muted in self._labels:
            try:
                label.configure(foreground=(
                    palette["muted"] if muted else palette["fg"]
                ))
            except tk.TclError:
                pass
        for label in (self.key_status_label,):
            try:
                label.configure(foreground=palette["muted"])
            except tk.TclError:
                pass
        for name in self._provider_status:
            self._style_provider_status(name)
        for name, enabled in self._provider_enabled.items():
            self._set_provider_row_enabled(name, enabled.get())
        for kind in ("movie", "tv"):
            self._recolor_format_panel(kind, palette)
            self._refresh_preset_cards(kind)
        self._set_key_status(
            self.key_status_var.get(),
        )

    def close(self):
        """Stop scheduled UI work and background network jobs."""
        self._closed = True
        root = self._root()
        for sequence, binding_id in self._scroll_bindings:
            root._unbind(("bind", "all", sequence), binding_id)
        self._scroll_bindings.clear()
        if self._key_after is not None:
            try:
                self.after_cancel(self._key_after)
            except tk.TclError:
                pass
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except tk.TclError:
                pass
        if self._saved_key_status_after is not None:
            try:
                self.after_cancel(self._saved_key_status_after)
            except tk.TclError:
                pass
        for after_id in getattr(self, "_provider_key_check_after", {}).values():
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        for variable, trace_id in getattr(self, "_preference_traces", ()):
            try:
                variable.trace_remove("write", trace_id)
            except tk.TclError:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
