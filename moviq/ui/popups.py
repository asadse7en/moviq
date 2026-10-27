import ctypes
import sys
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import ttk

from .. import resource
from .controls import create_action_button
from .window_utils import set_window_icon


def apply_titlebar_theme(window, dark):
    if sys.platform != "win32":
        return
    window._moviq_titlebar_theme = (
        dark,
        (
            {"caption": "#1c1c1c", "text": "#edf2f7", "border": "#1c1c1c"}
            if dark else
            {"caption": "#fafafa", "text": "#1f2937", "border": "#fafafa"}
        ),
    )

    def color_ref(color):
        red, green, blue = (
            int(color[index:index + 2], 16) for index in (1, 3, 5)
        )
        return red | (green << 8) | (blue << 16)

    def apply():
        try:
            if not window.winfo_exists():
                return
            dark, palette = window._moviq_titlebar_theme
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            get_ancestor = user32.GetAncestor
            get_ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
            get_ancestor.restype = wintypes.HWND
            widget_hwnd = wintypes.HWND(window.winfo_id())
            hwnd = get_ancestor(widget_hwnd, 2) or widget_hwnd

            dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            set_attribute = dwmapi.DwmSetWindowAttribute
            set_attribute.argtypes = (
                wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                wintypes.DWORD,
            )
            set_attribute.restype = ctypes.c_long

            dark_value = wintypes.BOOL(dark)
            result = set_attribute(
                hwnd, 20, ctypes.byref(dark_value),
                ctypes.sizeof(dark_value),
            )
            if result != 0:
                set_attribute(
                    hwnd, 19, ctypes.byref(dark_value),
                    ctypes.sizeof(dark_value),
                )

            for attribute, key in (
                (35, "caption"),
                (36, "text"),
                (34, "border"),
            ):
                color = wintypes.DWORD(color_ref(palette[key]))
                set_attribute(
                    hwnd, attribute, ctypes.byref(color), ctypes.sizeof(color),
                )
        except (AttributeError, OSError, tk.TclError):
            return

    apply()


class MatchProgressPopup:
    def __init__(self, app, on_cancel, on_rename, on_review, on_close):
        self.app = app
        self.on_cancel = on_cancel
        self.on_rename = on_rename
        self.on_review = on_review
        self.on_close = on_close
        self.palette = app.pal
        self.completed = False

        self.backdrop = tk.Toplevel(app.root)
        set_window_icon(self.backdrop)
        self.backdrop.withdraw()
        self.backdrop.overrideredirect(True)
        self.backdrop.configure(bg="#000000")
        self.backdrop.attributes("-alpha", 0.6)
        self.backdrop.transient(app.root)
        self.backdrop.bind("<Button-1>", lambda _event: "break")

        self.window = tk.Toplevel(app.root)
        set_window_icon(self.window)
        self.window.withdraw()
        self.window.title("Matching Files with TMDb")
        self.window.configure(bg=self.palette["bg"])
        self.window.transient(app.root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self._window_close)
        self.window.bind("<Escape>", lambda _event: self._window_close())
        self._configure_styles()
        self.current_font = tkfont.nametofont("TkTextFont")
        self._build_content()
        self._center()
        self._position_backdrop()
        self._configure_binding = app.root.bind(
            "<Configure>", self._root_configured, add="+",
        )
        self.backdrop.deiconify()
        self.window.deiconify()
        self.backdrop.lift()
        self.window.lift()
        apply_titlebar_theme(self.window, app.mode == "dark")
        self.window.grab_set()
        self.window.focus_set()

    def _configure_styles(self):
        palette = self.palette
        style = ttk.Style(self.window)
        style.configure("MatchPopup.TFrame", background=palette["card"])
        style.configure(
            "MatchPopup.Title.TLabel",
            background=palette["card"],
            foreground=palette["fg"],
            font="TkHeadingFont",
        )
        style.configure(
            "MatchPopup.Muted.TLabel",
            background=palette["card"],
            foreground=palette["muted"],
            font="TkTextFont",
        )
        style.configure(
            "MatchPopup.Count.TLabel",
            background=palette["card"],
            foreground=palette["fg"],
            font="TkHeadingFont",
        )
    def _build_content(self):
        palette = self.palette
        card = ttk.Frame(
            self.window,
            style="MatchPopup.TFrame",
            padding=(22, 18, 22, 14),
        )
        self.card = card
        card.pack(fill="both", expand=True)
        card.columnconfigure(1, weight=1)

        header = ttk.Frame(card, style="MatchPopup.TFrame")
        header.grid(row=0, column=1, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.icon_label = tk.Label(
            card,
            text="\u27f3",
            bg=palette["card"],
            fg=palette["accent"],
            font="MoviqTitleFont",
            width=2,
        )
        self.icon_label.grid(
            row=0, column=0, rowspan=3, sticky="n", padx=(0, 12),
        )
        self.title_var = tk.StringVar(value="Matching Files with TMDb")
        ttk.Label(
            header,
            textvariable=self.title_var,
            style="MatchPopup.Title.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.percent_var = tk.StringVar(value="0%")
        ttk.Label(
            header,
            textvariable=self.percent_var,
            style="MatchPopup.Count.TLabel",
        ).grid(row=0, column=1, sticky="e", padx=(12, 0))

        self.current_var = tk.StringVar(value="Preparing files...")
        ttk.Label(
            card,
            textvariable=self.current_var,
            style="MatchPopup.Muted.TLabel",
            wraplength=0,
        ).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=(6, 10),
        )

        self.progress_canvas = tk.Canvas(
            card,
            height=12,
            bg=palette["card"],
            bd=0,
            highlightthickness=0,
        )
        self.progress_canvas.grid(
            row=2, column=1, columnspan=2, sticky="ew",
        )
        self.progress_canvas.bind(
            "<Configure>", self._draw_progress, add="+",
        )
        self._progress_value = 0.0
        self._progress_target = 0.0
        self._progress_after = None

        counters = ttk.Frame(card, style="MatchPopup.TFrame")
        counters.grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(12, 0),
        )
        for index in range(3):
            counters.columnconfigure(index, weight=1)
        self.counter_vars = [
            tk.StringVar(value="Matched  0"),
            tk.StringVar(value="Needs Review  0"),
            tk.StringVar(value="Failed  0"),
        ]
        counter_colors = (
            palette["status_colors"]["auto"][0],
            palette["status_colors"]["review"][0],
            palette["status_colors"]["error"][0],
        )
        for index, (value, color) in enumerate(
            zip(self.counter_vars, counter_colors),
        ):
            label = ttk.Label(
                counters,
                textvariable=value,
                style="MatchPopup.Count.TLabel",
                foreground=color,
                anchor="center",
            )
            label.grid(row=0, column=index, sticky="ew", padx=(0, 8))

        self.footer = ttk.Frame(card, style="MatchPopup.TFrame")
        self.footer.grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(16, 0),
        )
        self._set_running_footer()

    def _draw_progress(self, _event=None):
        canvas = self.progress_canvas
        if not canvas.winfo_exists():
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        radius = height / 2
        canvas.delete("all")

        def capsule(end, color):
            end = max(0, min(width, end))
            if end <= 0:
                return
            if end <= height:
                canvas.create_oval(
                    0, 0, end, height, fill=color, outline="",
                )
                return
            canvas.create_oval(
                0, 0, height, height, fill=color, outline="",
            )
            canvas.create_rectangle(
                radius, 0, max(radius, end - radius), height,
                fill=color, outline="",
            )
            if end >= height:
                canvas.create_oval(
                    end - height, 0, end, height, fill=color, outline="",
                )

        capsule(width, self.palette["btn"])
        capsule(width * self._progress_value, self.palette["accent"])

    def _animate_progress(self):
        self._progress_after = None
        delta = self._progress_target - self._progress_value
        if abs(delta) < 0.004:
            self._progress_value = self._progress_target
        else:
            self._progress_value += delta * 0.28
        self._draw_progress()
        if self._progress_value != self._progress_target:
            self._progress_after = self.progress_canvas.after(
                16, self._animate_progress,
            )

    def _set_progress(self, percent):
        self._progress_target = max(0.0, min(1.0, percent / 100))
        if self._progress_after is None:
            self._animate_progress()

    def _truncate_current_text(self, text):
        if self.current_font.measure(text) <= 440:
            return text
        shortened = text
        while shortened and self.current_font.measure(
            shortened + "...",
        ) > 440:
            shortened = shortened[:-1]
        return f"{shortened.rstrip()}..." if shortened else "..."

    def _set_running_footer(self):
        for child in self.footer.winfo_children():
            child.destroy()
        self._footer_button(
            self.footer,
            "Cancel",
            self.on_cancel,
        ).pack(side="right")

    def _set_completed_footer(self, matched, flagged):
        for child in self.footer.winfo_children():
            child.destroy()
        self._footer_button(
            self.footer,
            "Close",
            self._close,
        ).pack(side="right")
        self._footer_button(
            self.footer,
            "Review Flagged",
            self._review,
            state="normal" if flagged else "disabled",
        ).pack(side="right", padx=(0, 8))
        self._footer_button(
            self.footer,
            "Rename Matched",
            self._rename,
            primary=True,
            state="normal" if matched else "disabled",
        ).pack(side="right", padx=(0, 8))

    def _footer_button(
        self, parent, text, command, primary=False, state="normal",
    ):
        return create_action_button(
            parent, text, command, self.palette, primary, state,
            self.app.UI_SCALE,
        )

    def update_progress(
        self, current, completed, total, matched, flagged, failed,
    ):
        if self.completed:
            return
        total = max(total, 1)
        percent = min(100, int(100 * completed / total))
        self.percent_var.set(f"{percent}%")
        self._set_progress(percent)
        current_text = (
            f"Matching file {min(completed + 1, total)} of {total}: {current}"
            if current else f"Matching {completed} of {total} files..."
        )
        self.current_var.set(self._truncate_current_text(current_text))
        self.counter_vars[0].set(f"Matched  {matched}")
        self.counter_vars[1].set(f"Needs Review  {flagged}")
        self.counter_vars[2].set(f"Failed  {failed}")

    def complete(
        self, completed, total, matched, flagged, failed, cancelled=False,
    ):
        self.completed = True
        self.title_var.set("Matching Complete")
        total = max(total, 1)
        percent = 100 if not cancelled else min(
            100, int(100 * completed / total),
        )
        self.percent_var.set(f"{percent}%")
        self._set_progress(percent)
        self.counter_vars[0].set(f"Matched  {matched}")
        self.counter_vars[1].set(f"Needs Review  {flagged}")
        self.counter_vars[2].set(f"Failed  {failed}")
        self.current_var.set(
            "Matching canceled." if cancelled else "All matching tasks finished."
        )
        self._set_completed_footer(matched, flagged)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        self.window.bind("<Escape>", lambda _event: self._close())
        self._center()
        self.window.grab_release()
        self.backdrop.withdraw()

    def _center(self):
        self.app.root.update_idletasks()
        self.window.update_idletasks()
        parent = self.app.root
        width = max(480, self.card.winfo_reqwidth())
        height = max(190, self.card.winfo_reqheight() + 2)
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _position_backdrop(self):
        self.app.root.update_idletasks()
        root = self.app.root
        self.backdrop.geometry(
            f"{root.winfo_width()}x{root.winfo_height()}"
            f"+{root.winfo_rootx()}+{root.winfo_rooty()}",
        )

    def _root_configured(self, event):
        if event.widget == self.app.root and self.backdrop.winfo_exists():
            self._position_backdrop()
            self._center()

    def _window_close(self):
        if self.completed:
            self._close()
        else:
            self.on_cancel()

    def _rename(self):
        self._close()
        self.on_rename()

    def _review(self):
        self._close()
        self.on_review()

    def _close(self):
        if not self.window.winfo_exists():
            return
        self.window.grab_release()
        self.app.root.unbind("<Configure>", self._configure_binding)
        self.window.destroy()
        self.backdrop.destroy()
        self.on_close(self)


class AppPopup(tk.Toplevel):
    def __init__(self, app, title, message, kind, buttons, default, cancel_value):
        super().__init__(app.root)
        set_window_icon(self)
        self.app = app
        self.result = cancel_value
        self.buttons = buttons
        self.default = default
        self.default_value = buttons[default][1]
        self.cancel_value = cancel_value
        self.palette = app.pal
        self.title(title)
        self.configure(bg=self.palette["bg"])
        self.transient(app.root)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        self._configure_styles()
        self._build_content(title, message, kind)
        self.update_idletasks()
        apply_titlebar_theme(self, self.app.mode == "dark")
        self.bind("<Escape>", lambda _event: self._cancel())
        self.bind("<Return>", lambda _event: self._choose(self.default_value))
        self._center()
        self.grab_set()
        self.default_button.focus_set()
        self.wait_window()

    def _configure_styles(self):
        palette = self.palette
        style = ttk.Style(self)
        style.configure(
            "Popup.TFrame",
            background=palette["card"],
        )
        style.configure(
            "Popup.Divider.TSeparator",
            background=palette["border"],
        )
        style.configure(
            "Popup.Title.TLabel",
            background=palette["card"],
            foreground=palette["fg"],
            font="TkHeadingFont",
        )
        style.configure(
            "Popup.Message.TLabel",
            background=palette["card"],
            foreground=palette["fg"],
            font="TkDefaultFont",
        )
        style.configure(
            "Popup.Muted.TLabel",
            background=palette["card"],
            foreground=palette["muted"],
            font="TkTextFont",
        )

    def _build_content(self, title, message, kind):
        palette = self.palette
        ttk.Separator(
            self,
            orient="horizontal",
            style="Popup.Divider.TSeparator",
        ).pack(side="top", fill="x")
        card = ttk.Frame(self, style="Popup.TFrame", padding=(22, 20, 22, 16))
        card.pack(fill="both", expand=True)
        card.columnconfigure(1, weight=1)

        icon, color = {
            "info": ("\u2139", palette["accent"]),
            "warning": ("\u26a0", palette.get("warning", "#d97706")),
            "error": ("\u2715", "#dc2626"),
            "question": ("?", palette["accent"]),
        }[kind]
        icon_label = tk.Label(
            card,
            text=icon,
            bg=palette["card"],
            fg=color,
            font="MoviqTitleFont",
            width=2,
        )
        icon_label.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 12))
        ttk.Label(
            card, text=title, style="Popup.Title.TLabel",
        ).grid(row=0, column=1, sticky="w", pady=(0, 8))
        body = ttk.Label(
            card,
            text=message,
            style="Popup.Message.TLabel",
            wraplength=390,
            justify="left",
        )
        body.grid(row=1, column=1, sticky="w")

        footer = ttk.Frame(card, style="Popup.TFrame")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(20, 0))
        for index, (label, value) in enumerate(self.buttons):
            is_default = index == self.default
            button = create_action_button(
                footer,
                label,
                lambda selected=value: self._choose(selected),
                palette,
                primary=is_default,
                ui_scale=self.app.UI_SCALE,
            )
            button.pack(side="right", padx=(8, 0))
            if index == self.default:
                self.default_button = button

        self.update_idletasks()
        height = max(178, min(440, card.winfo_reqheight() + 2))
        self.geometry(f"480x{height}")

    def _center(self):
        self.update_idletasks()
        parent = self.app.root
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")

    def _choose(self, value):
        self.result = value
        self.destroy()

    def _cancel(self):
        self.result = self.cancel_value
        self.destroy()


def _show(app, title, message, kind, buttons, default, cancel_value):
    popup = AppPopup(
        app, title, message, kind, buttons, default, cancel_value,
    )
    return popup.result


def show_info(app, title, message):
    _show(app, title, message, "info", [("OK", True)], 0, True)


def show_warning(app, title, message):
    _show(app, title, message, "warning", [("OK", True)], 0, True)


def show_error(app, title, message):
    _show(app, title, message, "error", [("OK", True)], 0, True)


def ask_yes_no(app, title, message):
    return _show(
        app, title, message, "question",
        [("No", False), ("Yes", True)], 1, False,
    )


def ask_ok_cancel(app, title, message):
    return _show(
        app, title, message, "question",
        [("Cancel", False), ("OK", True)], 1, False,
    )


def ask_yes_no_cancel(app, title, message):
    return _show(
        app, title, message, "question",
        [("Cancel", None), ("No", False), ("Yes", True)], 2, None,
    )


class RenameConfirmationDialog:
    def __init__(self, app, summary):
        self.result = _show(
            app,
            "Confirm rename",
            summary,
            "question",
            [("Cancel", False), ("Rename", True)],
            1,
            False,
        )
