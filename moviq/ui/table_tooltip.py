import tkinter as tk
import tkinter.font as tkfont

from .popups import set_window_icon


class TableTooltip:
    def __init__(self, master, background, foreground, border, muted=None):
        self.master = master
        self.background = background
        self.foreground = foreground
        self.border = border
        self.muted = muted or foreground
        self._after_id = None
        self._window = None
        self._key = None

    def set_theme(self, background, foreground, border, muted=None):
        self.background = background
        self.foreground = foreground
        self.border = border
        self.muted = muted or foreground
        self.hide()

    def schedule(self, key, content, x, y, delay=400):
        if key == self._key:
            return
        if self._after_id is not None:
            self.master.after_cancel(self._after_id)
            self._after_id = None
        if not content:
            self.hide()
            return
        self._key = key
        self._after_id = self.master.after(
            delay, lambda: self._show(key, content, x, y),
        )

    def _show(self, key, content, x, y):
        self._after_id = None
        if key != self._key:
            return
        window = self._window
        if window is None:
            window = tk.Toplevel(self.master)
            set_window_icon(window)
            window.wm_overrideredirect(True)
            window.configure(background=self.border)
            self._window = window
        for child in window.winfo_children():
            child.destroy()
        panel = tk.Frame(window, background=self.background, padx=9, pady=7)
        panel.pack(padx=1, pady=1)
        screen_width = self.master.winfo_screenwidth()
        if isinstance(content, str):
            tk.Label(
                panel, text=content, justify="left", anchor="w",
                background=self.background, foreground=self.foreground,
                wraplength=480,
            ).pack()
        else:
            normal_font = tkfont.nametofont("TkTextFont")
            max_width = min(800, max(240, screen_width - 16))
            label_width = min(
                max(
                    normal_font.measure(label)
                    for label, _value in content
                ) + 8,
                130,
            )
            value_width = max(
                normal_font.measure(value) for _label, value in content
            )
            card_width = min(
                max_width,
                max(420, label_width + value_width + 32),
            )
            wraplength = max(80, card_width - label_width - 32)
            label_font = (
                normal_font.actual("family"),
                normal_font.actual("size"),
                "bold",
            )
            panel.grid_columnconfigure(0, minsize=label_width, weight=0)
            panel.grid_columnconfigure(1, weight=1)
            for row, (label, value) in enumerate(content):
                if not label:
                    tk.Label(
                        panel, text=value, justify="left", anchor="w",
                        background=self.background, foreground=self.muted,
                        font=normal_font, wraplength=wraplength,
                    ).grid(
                        row=row, column=0, columnspan=2, sticky="ew",
                        pady=(2, 0),
                    )
                    continue
                color = (
                    self.muted
                    if label in ("How", "Year", "Moves with it")
                    else self.foreground
                )
                tk.Label(
                    panel, text=label, justify="left", anchor="nw",
                    background=self.background, foreground=self.foreground,
                    font=label_font,
                ).grid(row=row, column=0, sticky="nw", padx=(0, 8))
                tk.Label(
                    panel, text=value, justify="left", anchor="nw",
                    background=self.background, foreground=color,
                    font=normal_font, wraplength=wraplength,
                ).grid(row=row, column=1, sticky="ew")
        window.update_idletasks()
        screen_height = self.master.winfo_screenheight()
        screen_x = x + 12
        if screen_x + window.winfo_width() > screen_width - 8:
            screen_x = x - window.winfo_width() - 12
        screen_x = max(8, min(screen_x, screen_width - window.winfo_width() - 8))
        screen_y = y + 18
        if screen_y + window.winfo_height() > screen_height - 8:
            screen_y = y - window.winfo_height() - 12
        screen_y = max(8, min(screen_y, screen_height - window.winfo_height() - 8))
        window.wm_geometry(f"+{screen_x}+{screen_y}")

    def hide(self, _event=None):
        if self._after_id is not None:
            self.master.after_cancel(self._after_id)
            self._after_id = None
        self._key = None
        if self._window is not None:
            self._window.destroy()
            self._window = None
