import sys
import tkinter as tk

from .. import resource


def wheel_steps(event):
    delta = int(getattr(event, "delta", 0))
    if sys.platform == "win32":
        if abs(delta) >= 120:
            return -int(delta / 120)
        return -1 if delta > 0 else 1 if delta < 0 else 0
    if sys.platform == "darwin":
        return delta
    return 0


def shortcut_variants(sequence):
    variants = [sequence]
    if sys.platform == "darwin" and "<Control-" in sequence:
        variants.append(sequence.replace("<Control-", "<Command-"))
    return variants


def set_window_icon(window):
    try:
        window._moviq_icon = tk.PhotoImage(
            master=window,
            file=resource("assets/moviq-icon.png"),
        )
        window.iconphoto(True, window._moviq_icon)
    except (OSError, tk.TclError):
        pass
