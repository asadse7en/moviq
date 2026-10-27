import sqlite3
import tkinter as tk
import webbrowser
from tkinter import ttk

from PIL import Image, ImageTk

from ... import resource
from ...core.tmdb_cache import clear_tmdb_cache
from ..popups import show_error, show_info
from ._common import (
    APP_VERSION, ISSUES_URL, LICENSE_URL, RELEASES_URL, check_latest_release,
)


class AboutTabMixin:
    def _build_about(self, frame):
        self._section_header(
            frame, "About", "Version information and project links.",
        )
        group = self._group(frame, "Moviq")
        with Image.open(resource("assets/moviq-icon.png")) as icon:
            icon = icon.resize((72, 72), Image.Resampling.LANCZOS)
            self.moviq_icon_image = ImageTk.PhotoImage(icon)
        self.about_icon_label = tk.Label(
            group,
            image=self.moviq_icon_image,
            background=self.app.pal["preset_surface"],
            borderwidth=0,
        )
        self.about_icon_label.grid(row=1, column=0, sticky="w", pady=(0, 0))
        self._settings_card_surfaces.append(self.about_icon_label)
        self._label(
            group, f"Moviq {APP_VERSION}",
            font="TkHeadingFont", style="Settings.Card.TLabel",
        ).grid(row=2, column=0, sticky="w", pady=5)
        self.update_button = ttk.Button(
            group,
            text="Check for updates",
            style="Settings.TButton",
            command=self._check_for_updates,
        )
        self.update_button.grid(
            row=1, column=1, sticky="e", padx=8, pady=7,
        )
        self.update_status_var = tk.StringVar(value="")
        self.update_status_label = self._label(
            group,
            textvariable=self.update_status_var,
            muted=True,
            style="Settings.Card.TLabel",
        )
        self.update_status_label.grid(
            row=2, column=1, sticky="e", padx=8, pady=4,
        )
        self.download_update_button = ttk.Button(
            group,
            text="Download",
            style="Settings.TButton",
            command=lambda: webbrowser.open(self._update_release_url),
        )
        self.download_update_button.grid(
            row=3, column=1, sticky="e", padx=8, pady=4,
        )
        self.download_update_button.grid_remove()
        links = tk.Frame(group, background=self.app.pal["preset_surface"])
        links.grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self._settings_card_surfaces.append(links)
        self._link(links, "Release notes", RELEASES_URL).pack(side="left", padx=(0, 16))
        self._link(links, "Report a problem", ISSUES_URL).pack(side="left", padx=(0, 16))
        self._link(links, "Open-source licenses", LICENSE_URL).pack(side="left")
        cache_group = self._group(frame, "Cache")
        self._label(
            cache_group,
            "Clear cached provider responses when testing with fresh data.",
            muted=True,
            style="Settings.Card.TLabel",
        ).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        ttk.Button(
            cache_group, text="Clear cache",
            style="Settings.TButton", command=self._clear_tmdb_cache,
        ).grid(row=1, column=1, sticky="e", pady=4)

    def _clear_tmdb_cache(self):
        try:
            count = clear_tmdb_cache()
        except (OSError, RuntimeError, sqlite3.Error) as error:
            show_error(
                self.app, "Couldn't clear cache",
                f"Provider response cache could not be cleared: {error}",
            )
            return
        show_info(
            self.app, "Cache cleared",
            f"Cleared {count} cached provider responses.",
        )

    def _check_for_updates(self):
        if self._update_checking:
            return
        self._update_checking = True
        self._update_release_url = None
        self.download_update_button.grid_remove()
        self.update_button.configure(state="disabled")
        self.update_status_var.set("Checking…")
        self.update_status_label.configure(foreground=self.app.pal["muted"])
        self._executor.submit(self._check_for_updates_worker)

    def _check_for_updates_worker(self):
        result = check_latest_release(APP_VERSION)
        self._queue.put(("update", *result))
