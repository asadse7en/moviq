import tkinter as tk
from tkinter import ttk


class MatchingTabMixin:
    def _build_matching(self, frame):
        self._section_header(
            frame, "Matching", "Control automatic matching and acceptance.",
        )
        group = self._group(frame, "Automatic matching")
        row = self._row(
            group, 0, "Auto-match new files",
            self._switch(group, self.vars["auto_match"]),
        )
        scale_wrap = ttk.Frame(group)
        scale = ttk.Scale(
            scale_wrap, from_=50, to=100, variable=self.vars["threshold"],
            command=self._threshold_moved, length=300,
        )
        scale.pack(side="left")
        self.threshold_label_var = tk.StringVar()
        self._label(
            scale_wrap, textvariable=self.threshold_label_var,
        ).pack(side="left", padx=10)
        self.threshold_hint_var = tk.StringVar()
        self._label(
            group, textvariable=self.threshold_hint_var, muted=True,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        scale_wrap.grid(row=row - 1, column=1, sticky="e", pady=7)
        self.vars["threshold"].trace_add(
            "write", lambda *_args: self._update_threshold_hint(),
        )
        self._update_threshold_hint()

    def _threshold_moved(self, value):
        snapped = min(100, max(50, int(round(float(value) / 5) * 5)))
        if self.vars["threshold"].get() != snapped:
            self.vars["threshold"].set(snapped)

    def _update_threshold_hint(self):
        threshold = min(
            100, max(50, int(round(self.vars["threshold"].get() / 5) * 5)),
        )
        if self.vars["threshold"].get() != threshold:
            self.vars["threshold"].set(threshold)
        self.threshold_label_var.set(f"{threshold}%")
        if threshold >= 90:
            hint = "Higher thresholds are stricter and send fewer matches to review."
        elif threshold >= 75:
            hint = "Balanced setting: uncertain matches are sent for review."
        else:
            hint = "Lower thresholds accept more matches automatically."
        self.threshold_hint_var.set(hint)
