import tkinter as tk

from .. import resource


def set_window_icon(window):
    try:
        window._moviq_icon = tk.PhotoImage(
            master=window,
            file=resource("assets/moviq-icon-256.png"),
        )
        window.iconphoto(True, window._moviq_icon)
    except (OSError, tk.TclError):
        pass
