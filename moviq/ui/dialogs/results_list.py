import tkinter as tk

from ...core.matching import rank, rank_tv


class ResultsListMixin:
    def _result_title(self, result):
        title = self._title(result, self.is_tv)
        if self.is_tv and result.get("_matched_episode_query"):
            title += (
                f"  ·  S{self.item.season_number:02d}"
                f"E{self.item.episode_number:02d}"
                f"  ·  {result['episode_title']}"
            )
        year = self._year(result) or result.get("year")
        if year:
            title += f" ({year})"
        return title

    def _show_search_results(self, generation, query, results, error):
        if generation != self._search_generation:
            return
        if error:
            self.search_status_var.set(f"Search failed: {error}")
            return
        self.results = (
            rank_tv(results, query) if self.is_tv else rank(results, query)
        )[:30]
        self._populate_result_cards()
        if not self.results:
            self.result_count_var.set("0")
            label = "TV shows" if self.is_tv else "movies"
            self.search_status_var.set(f"No matching {label} found.")
            self._clear_details()
            return
        self.result_count_var.set(str(len(self.results)))
        self.search_status_var.set(
            f"{len(self.results)} possible match(es).",
        )
        current_id = (self.item.match or {}).get("id")
        selected_index = next(
            (
                index for index, result in enumerate(self.results)
                if current_id is not None
                and str(result.get("id")) == str(current_id)
            ),
            0,
        )
        self._select_result(selected_index, focus=False)

    def _show_id_result(self, generation, tmdb_id, details, error):
        if generation != self._search_generation:
            return
        if error:
            self.search_status_var.set(f"TMDb lookup failed: {error}")
            return
        if not details:
            self.search_status_var.set(
                f"No {'TV show' if self.is_tv else 'movie'} found with "
                f"TMDb ID {tmdb_id}.",
            )
            self.result_count_var.set("0")
            self._clear_details()
            return
        self.results = [details]
        self._direct_id_search = True
        self.result_count_var.set("1")
        self.search_status_var.set(f"Loaded TMDb ID {tmdb_id}.")
        self._populate_result_cards()
        self.selected_index = 0
        self.selected_result = details
        self.selected_details = details
        self._requested_details_id = tmdb_id
        self._set_card_selection()
        self._show_selected_details(details)
        if self.is_tv:
            self.search_status_var.set(
                "Loading episode details…",
            )

    def _populate_result_cards(self):
        self._clear_result_cards()
        palette = self.app.pal
        for index, result in enumerate(self.results):
            card = tk.Frame(
                self.results_cards,
                bg=palette["card"],
                padx=5,
                pady=0,
                takefocus=True,
                highlightthickness=1,
                highlightbackground=palette["card"],
            )
            card.pack(fill="x", padx=(0, 5), pady=(0, 1))
            card.rowconfigure(0, weight=1)
            text_column = tk.Frame(card, bg=palette["card"])
            text_column.grid(row=0, column=0, sticky="nsew")
            text_column.columnconfigure(0, weight=1)
            text_column.rowconfigure(0, weight=1)
            card.columnconfigure(0, weight=1)
            title = tk.Label(
                text_column,
                text=self._result_title(result),
                bg=palette["card"],
                fg=palette["fg"],
                font="TkHeadingFont",
                anchor="w",
                justify="left",
                wraplength=650,
            )
            title.grid(row=0, column=0, sticky="w")
            self._result_widgets.append((card, title))
            for widget in (card, text_column, title):
                widget.bind(
                    "<Button-1>",
                    lambda _event, selected=index: self._select_result(
                        selected,
                    ),
                )
                widget.bind(
                    "<Double-Button-1>",
                    lambda _event, selected=index: self._double_click_result(
                        selected,
                    ),
                )
                widget.bind(
                    "<Up>",
                    lambda _event, selected=index: self._move_selection(
                        selected, -1,
                    ),
                )
                widget.bind(
                    "<Down>",
                    lambda _event, selected=index: self._move_selection(
                        selected, 1,
                    ),
                )
                widget.bind(
                    "<Return>",
                    lambda _event: self._confirm_selected(),
                )
                widget.bind("<Tab>", self._tab_from_results)
                widget.bind("<Shift-Tab>", self._tab_back_from_results)
                widget.bind(
                    "<space>",
                    lambda _event, selected=index: self._select_result(
                        selected,
                    ),
                )
            self._bind_results_mousewheel(card)
        self.results_cards.update_idletasks()
        self._fit_results_window()
        self._update_results_scrollregion()

    def _clear_result_cards(self):
        for child in self.results_cards.winfo_children():
            child.destroy()
        self._result_widgets = []
        self.results_canvas.yview_moveto(0)
        self._fit_results_window()

    def _set_card_selection(self):
        palette = self.app.pal
        selected_bg = palette.get("selection", palette["accent"])
        for index, widgets in enumerate(self._result_widgets):
            card, title = widgets
            is_selected = index == self.selected_index
            background = selected_bg if is_selected else palette["card"]
            foreground = (
                palette["fg"] if palette.get("selection") else "#ffffff"
            ) if is_selected else palette["fg"]
            card.configure(
                bg=background,
                highlightbackground=(
                    palette["accent"] if is_selected else palette["card"]
                ),
            )
            title.configure(bg=background, fg=foreground)
            children = widgets[0].winfo_children()
            children[0].configure(bg=background)

    def _select_result(self, index, focus=True, details=None):
        if not 0 <= index < len(self.results):
            return
        previous_details = self.selected_details
        same_result = (
            self.selected_result
            and self.selected_result.get("id") == self.results[index].get("id")
        )
        self.selected_index = index
        self.selected_result = self.results[index]
        self.selected_details = dict(details or self.selected_result)
        if self.is_tv and not self.selected_details.get("episode_title"):
            self.selected_details["_episode_mapping_pending"] = True
        if self.is_tv and same_result and previous_details:
            for key in (
                "episode_title", "_episode_error",
                "_episode_mapping_pending",
                "_absolute_unmapped", "_episode_season_number",
                "_episode_number", "_episode_end_number",
            ):
                if key in previous_details:
                    self.selected_details.setdefault(
                        key, previous_details[key],
                    )
        if self.is_tv and not self.selected_details.get("episode_title"):
            episode_title = self._known_episode_title(self.selected_result)
            if episode_title:
                self.selected_details["episode_title"] = episode_title
        self._set_card_selection()
        self._show_selected_details(self.selected_details)
        if focus:
            if index < len(self._result_widgets):
                self._result_widgets[index][0].focus_set()
                self._show_card(index)
        if details is not None:
            self._requested_details_id = self.selected_result.get("id")
            return
        movie_id = self.selected_result.get("id")
        if (
            self.is_tv
            and movie_id
            and not self.selected_details.get("episode_title")
            and self._requested_episode_id != movie_id
        ):
            self._requested_episode_id = movie_id
            self._executor.submit(
                self._load_episode, self._search_generation, movie_id,
            )
        if self._requested_details_id == movie_id:
            return
        self._requested_details_id = movie_id
        self._request_details(self.selected_result)

    def _known_episode_title(self, result):
        match = self.item.match or {}
        if (
            match.get("id") == result.get("id")
            and match.get("season_number") == self.item.season_number
            and match.get("episode_number") == self.item.episode_number
        ):
            return match.get("episode_title")
        return None

    def _show_card(self, index):
        if not 0 <= index < len(self._result_widgets):
            return
        card = self._result_widgets[index][0]
        self.results_canvas.update_idletasks()
        card_top = (
            card.winfo_rooty() - self.results_canvas.winfo_rooty()
            + self.results_canvas.canvasy(0)
        )
        card_bottom = card_top + card.winfo_height()
        view_top = self.results_canvas.canvasy(0)
        view_bottom = view_top + self.results_canvas.winfo_height()
        content_height = max(1, self.results_list.winfo_height())
        if card_top < view_top:
            self.results_canvas.yview_moveto(card_top / content_height)
        elif card_bottom > view_bottom:
            self.results_canvas.yview_moveto(
                max(0, (card_bottom - self.results_canvas.winfo_height()))
                / content_height,
            )

    def _double_click_result(self, index):
        self._select_result(index)
        self._confirm_selected()
        return "break"

    def _move_selection(self, index, amount):
        if not self.results:
            return "break"
        next_index = min(max(index + amount, 0), len(self.results) - 1)
        self._select_result(next_index)
        return "break"

    def _focus_first_result(self, _event=None):
        if self.results:
            self._select_result(0)
        else:
            self.cancel_button.focus_set()
        return "break"

    def _focus_widget(self, widget):
        widget.focus_set()
        return "break"

    def _tab_from_search(self, _event=None):
        if self.results:
            index = (
                self.selected_index if self.selected_index is not None else 0
            )
            self._select_result(index)
        else:
            self.cancel_button.focus_set()
        return "break"

    def _tab_back_from_search(self, _event=None):
        self.update_button.focus_set()
        return "break"

    def _tab_from_results(self, _event=None):
        self.cancel_button.focus_set()
        return "break"

    def _tab_back_from_results(self, _event=None):
        self.search_entry.focus_set()
        return "break"

    def _tab_back_from_action(self, _event=None):
        if self.results:
            index = (
                self.selected_index if self.selected_index is not None else 0
            )
            self._select_result(index)
        else:
            self.search_entry.focus_set()
        return "break"

    def _confirm_selected(self):
        if str(self.update_button.cget("state")) != "disabled":
            self.update_match()
        return "break"

    def _update_results_scrollregion(self, _event=None):
        self.results_canvas.configure(
            scrollregion=self.results_canvas.bbox("all"),
        )

    def _resize_results_list(self, event):
        self.results_canvas.itemconfigure(
            self._results_window, width=event.width,
        )
        self._fit_results_window()

    def _fit_results_window(self, _event=None):
        if not hasattr(self, "results_canvas"):
            return
        view_height = self.results_canvas.winfo_height()
        content_height = self.results_cards.winfo_reqheight()
        window_height = max(view_height, content_height)
        self.results_canvas.itemconfigure(
            self._results_window,
            width=max(1, self.results_canvas.winfo_width()),
            height=max(1, window_height),
        )
        self._update_results_scrollregion()

    def _bind_results_mousewheel(self, widget):
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind(
            "<Button-4>",
            lambda _event: self._scroll_results_from_mouse(-1),
            add="+",
        )
        widget.bind(
            "<Button-5>",
            lambda _event: self._scroll_results_from_mouse(1),
            add="+",
        )
        for child in widget.winfo_children():
            self._bind_results_mousewheel(child)

    def _on_mousewheel(self, event):
        delta = event.delta
        units = -int(delta / 120) if abs(delta) >= 120 else (
            -1 if delta > 0 else 1 if delta < 0 else 0
        )
        self._scroll_results(units)
        return "break"

    def _scroll_results_from_mouse(self, units):
        self._scroll_results(units)
        return "break"

    def _scroll_results(self, units):
        if units:
            self.results_canvas.yview_scroll(units, "units")
