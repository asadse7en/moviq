import tkinter.font as tkfont


class HeaderLayout:
    def __init__(self, app, header):
        self.app = app
        self.header = header
        self.level = None
        self.widths = (0, 0, 0, 0, 0)
        self.measurements = {}
        self._after_id = None
        self._measuring = False
        self._last_available_width = None
        header.bind("<Configure>", self._schedule_relayout, add="+")
        for variable in (
            app.match_btn_var, app.manual_match_btn_var, app.apply_btn_var,
        ):
            variable.trace_add("write", self._schedule_relayout)
        self._schedule_relayout()

    @property
    def l0_width(self):
        return self.widths[0]

    @property
    def l4_width(self):
        return self.widths[4]

    def _schedule_relayout(self, *_args):
        if self._measuring or self._after_id is not None:
            return
        if _args and hasattr(_args[0], "widget"):
            event = _args[0]
            if event.width == self._last_available_width:
                return
            self._last_available_width = event.width
        self._after_id = self.header.after_idle(self._relayout_after_idle)

    def _relayout_after_idle(self):
        self._after_id = None
        self.remeasure()

    def _flush_geometry(self):
        self.app.root.update_idletasks()

    def _measure_widget(self, widget):
        return widget.winfo_reqwidth()

    def remeasure(self):
        if not self.header.winfo_exists() or self._measuring:
            return
        self._measuring = True
        app = self.app
        previous_level = self.level
        full_labels = getattr(
            app, "_full_match_button_labels",
            (app.match_btn_var.get(), app.manual_match_btn_var.get()),
        )
        try:
            app.header_subtitle.grid()
            app.add_files_button.set_icon(None)
            app.add_folder_button.set_icon(None)
            app.toolbar_icon_group.set_visible_actions(
                tuple(app.toolbar_icon_group.states),
            )
            app.overflow_button.grid_remove()
            app.match_btn_var.set(full_labels[0])
            app.manual_match_btn_var.set(full_labels[1])
            self._flush_geometry()

            measurements = {
                "logo_title": self._measure_widget(app.header_title_line),
                "brand": self._measure_widget(app.header_brand),
                "subtitle": self._measure_widget(app.header_subtitle),
                "add_files": self._measure_widget(app.add_files_button),
                "add_folder": self._measure_widget(app.add_folder_button),
                "mode_toggle": self._measure_widget(
                    app.media_mode_selector,
                ),
                "auto_match": self._measure_widget(app.auto_match_button),
                "manual_match": self._measure_widget(
                    app.manual_match_button,
                ),
                "rename_all": self._measure_widget(app.rename_all_button),
                "icon_group": self._measure_widget(app.toolbar_icon_group),
                "mode_separator": self._measure_widget(
                    app.header_mode_separator,
                ),
                "match_separator": self._measure_widget(
                    app.header_match_separator,
                ),
                "icon_separator": self._measure_widget(
                    app.header_icon_separator,
                ),
            }
            actions_full = self._measure_widget(app.header_actions)
            icons_full = self._measure_widget(app.header_icons)

            app.header_subtitle.grid_remove()
            self._flush_geometry()
            brand_without_subtitle = self._measure_widget(app.header_brand)

            app.add_files_button.set_icon("file-plus")
            app.add_folder_button.set_icon("folder-plus")
            self._flush_geometry()
            measurements["add_files_icon"] = self._measure_widget(
                app.add_files_button,
            )
            measurements["add_folder_icon"] = self._measure_widget(
                app.add_folder_button,
            )
            actions_icons = self._measure_widget(app.header_actions)

            app.match_btn_var.set("Auto")
            app.manual_match_btn_var.set("Manual")
            self._flush_geometry()
            measurements["auto_match_short"] = self._measure_widget(
                app.auto_match_button,
            )
            measurements["manual_match_short"] = self._measure_widget(
                app.manual_match_button,
            )
            actions_icons_short = self._measure_widget(app.header_actions)

            app.toolbar_icon_group.set_visible_actions(("settings",))
            app.overflow_button.grid()
            self._flush_geometry()
            measurements["icon_group_settings"] = self._measure_widget(
                app.toolbar_icon_group,
            )
            icons_overflow = self._measure_widget(app.header_icons)

            widths = (
                measurements["brand"] + actions_full + icons_full,
                brand_without_subtitle + actions_full + icons_full,
                brand_without_subtitle + actions_icons + icons_full,
                brand_without_subtitle + actions_icons + icons_overflow,
                max(
                    brand_without_subtitle + icons_overflow,
                    actions_icons_short,
                ),
            )
            self.widths = tuple(round(width) for width in widths)
            self.measurements = measurements
        finally:
            app.header_subtitle.grid(
                **({"row": 1, "column": 0, "columnspan": 2,
                   "sticky": "w", "padx": (app.header_subtitle_indent, 20)}
                  if previous_level in (None, 0) else {}),
            )
            if previous_level not in (None, 0):
                app.header_subtitle.grid_remove()
            app.add_files_button.set_icon(
                "file-plus" if previous_level is not None
                and previous_level >= 2 else None,
            )
            app.add_folder_button.set_icon(
                "folder-plus" if previous_level is not None
                and previous_level >= 2 else None,
            )
            if previous_level is not None and previous_level >= 3:
                app.toolbar_icon_group.set_visible_actions(("settings",))
                app.overflow_button.grid()
            else:
                app.toolbar_icon_group.set_visible_actions(
                    tuple(app.toolbar_icon_group.states),
                )
                app.overflow_button.grid_remove()
            app.match_btn_var.set(
                "Auto" if previous_level == 4 else full_labels[0],
            )
            app.manual_match_btn_var.set(
                "Manual" if previous_level == 4 else full_labels[1],
            )
            self._measuring = False
            self._flush_geometry()

        self._set_level(self._choose_level(self.header.winfo_width()))
        self._last_available_width = self.header.winfo_width()
        if hasattr(app, "_window_min_height"):
            app.root.minsize(
                min(
                    self.minimum_window_width(),
                    app.root.winfo_screenwidth(),
                ),
                min(
                    app._window_min_height,
                    app.root.winfo_screenheight(),
                ),
            )
        self.update_overflow_menu()

    def _choose_level(self, available):
        for level, required in enumerate(self.widths):
            if required <= available:
                return level
        return 4

    def _set_level(self, level):
        if level == self.level:
            return
        self.level = level
        app = self.app
        app.header_subtitle.grid()
        if level:
            app.header_subtitle.grid_remove()
        app.add_files_button.set_icon(
            "file-plus" if level >= 2 else None,
        )
        app.add_folder_button.set_icon(
            "folder-plus" if level >= 2 else None,
        )
        if level >= 3:
            app.toolbar_icon_group.set_visible_actions(("settings",))
            app.overflow_button.grid(row=0, column=2, padx=(8, 8))
        else:
            app.toolbar_icon_group.set_visible_actions(
                tuple(app.toolbar_icon_group.states),
            )
            app.overflow_button.grid_remove()

        for frame in (app.header_brand, app.header_actions, app.header_icons):
            frame.grid_remove()
        for column in range(4):
            app.header.grid_columnconfigure(column, weight=0, minsize=0)
        for column in range(8):
            app.header_actions.grid_columnconfigure(
                column, weight=0, minsize=0,
            )

        if level < 4:
            app.header_brand.grid(row=0, column=0, sticky="w")
            app.header_actions.grid(row=0, column=1, sticky="w")
            app.header.grid_columnconfigure(2, weight=1)
            app.header_icons.grid(row=0, column=3, sticky="e")
        else:
            app.header_brand.grid(row=0, column=0, sticky="w")
            app.header_icons.grid(row=0, column=1, sticky="e")
            app.header_actions.grid(
                row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0),
            )
            app.header.grid_columnconfigure(1, weight=1)
            app.header_actions.grid_columnconfigure(2, weight=1)
        labels = getattr(
            app, "_full_match_button_labels",
            (app.match_btn_var.get(), app.manual_match_btn_var.get()),
        )
        auto_label, manual_label = (
            ("Auto", "Manual") if level == 4 else labels
        )
        if app.match_btn_var.get() != auto_label:
            app.match_btn_var.set(auto_label)
        if app.manual_match_btn_var.get() != manual_label:
            app.manual_match_btn_var.set(manual_label)

    def filter_row_min_width(self):
        app = self.app
        filter_width = max(
            app.filter_count_width,
            self._measure_widget(app.filter_control),
        )
        return (
            filter_width
            + round(20 * app.UI_SCALE) + self.search_min_width
            + self._measure_widget(app.source_control_label)
            + 12 + 7 + 8
            + self._measure_widget(app.source_control)
        )

    @property
    def search_min_width(self):
        scale = self.app.UI_SCALE
        text_width = tkfont.nametofont("TkDefaultFont").measure("Search")
        return round(36 * scale) + text_width + round(16 * scale)

    def minimum_window_width(self):
        return max(self.l4_width, self.filter_row_min_width()) + 40

    def default_window_width(self):
        return max(round(1180 * self.app.UI_SCALE), self.l0_width + 40)

    def update_overflow_menu(self):
        app = self.app
        if not hasattr(app, "overflow_menu"):
            return
        names = {"light": "Light", "dark": "Dark", "system": "System"}
        app.overflow_menu.entryconfigure(
            2, label=f"Theme: {names[app.theme_preference]}",
        )
        states = app.toolbar_icon_group.states
        app.overflow_menu.entryconfigure(
            0, state=states.get("undo", "disabled"),
        )
        app.overflow_menu.entryconfigure(
            1, state=states.get("clear", "disabled"),
        )
        app.overflow_menu.entryconfigure(
            2, state=states.get("theme", "normal"),
        )

    def popup_overflow_menu(self):
        self.update_overflow_menu()
        button = self.app.overflow_button
        menu = self.app.overflow_menu
        try:
            menu.tk_popup(
                button.winfo_rootx(),
                button.winfo_rooty() + button.winfo_height(),
            )
        finally:
            menu.grab_release()
