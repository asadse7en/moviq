from ._common import DETAIL_POSTER_SIZE
from ...core.providers.ids import display_id


class DetailsPanelMixin:
    def _season_episode(self, show_id):
        season = self.tmdb.season(show_id, self.item.season_number) or {}
        for episode in season.get("episodes") or []:
            if episode.get("episode_number") == self.item.episode_number:
                episode = dict(episode)
                episode.setdefault("season_number", self.item.season_number)
                return episode
        return None

    def _episode_details_for_show(self, show_id):
        numbers = self.item.episode_numbers or (self.item.episode_number,)
        season_number = self.item.season_number
        use_absolute = not self.item.season_was_explicit
        if not use_absolute:
            season = self.tmdb.season(show_id, season_number) or {}
            use_absolute = (
                self.item.episode_number
                > len(season.get("episodes") or [])
            )
        if use_absolute:
            episodes = [
                self.tmdb.absolute_episode(show_id, number)
                for number in numbers
            ]
            if (
                not all(episodes)
                or len({
                    episode.get("season_number") for episode in episodes
                }) != 1
                or any(
                    not isinstance(episode.get("episode_number"), int)
                    for episode in episodes
                )
                or any(
                    episodes[index]["episode_number"]
                    != episodes[0]["episode_number"] + index
                    for index in range(len(episodes))
                )
            ):
                return None, (
                    f"Absolute episode number {self.item.episode_number} "
                    "couldn't be mapped."
                ), None
            season_number = episodes[0]["season_number"]
            episode_numbers = [
                episode["episode_number"] for episode in episodes
            ]
        else:
            episode_numbers = list(numbers)
            season = self.tmdb.season(show_id, season_number) or {}
            by_number = {
                episode.get("episode_number"): episode
                for episode in season.get("episodes") or []
            }
            episodes = [by_number.get(number) for number in episode_numbers]
            if not all(episodes):
                return None, (
                    "TMDb episode information is unavailable for "
                    f"S{season_number:02d}E{self.item.episode_number:02d}."
                ), None
        titles = [episode.get("name") for episode in episodes]
        if not all(titles):
            return None, "TMDb episode titles are unavailable.", None
        title = " & ".join(titles)
        end_number = (
            episode_numbers[-1] if len(episode_numbers) > 1 else None
        )
        return title, None, (season_number, episode_numbers[0], end_number)

    def _request_details(self, result):
        self._executor.submit(
            self._load_details, self._search_generation, result,
        )

    def _load_details(self, generation, result):
        tmdb_id = result.get("id")
        details = None
        error = None
        media = dict(result)
        try:
            if self.is_tv:
                details = self.tmdb.tv(tmdb_id) if tmdb_id else None
            else:
                details = self.tmdb.movie(tmdb_id) if tmdb_id else None
            if details is None:
                error = "TMDb did not return details."
            if details:
                media.update(details)
        except Exception as exception:
            error = str(exception)
        self._result_queue.put((
            "details",
            generation,
            tmdb_id,
            media,
            error,
        ))

    def _load_episode(self, generation, tmdb_id):
        try:
            episode_title, error, coordinates = (
                self._episode_details_for_show(tmdb_id)
            )
        except Exception as exception:
            episode_title = None
            error = str(exception)
            coordinates = None
        self._result_queue.put((
            "episode", generation, tmdb_id, episode_title, error,
            coordinates,
        ))

    def _show_selected_details(self, details):
        result = details or {}
        title = self._title(result, self.is_tv)
        year = self._year(result) or result.get("year")
        self.details_title_var.set(
            f"{title}{f' ({year})' if year else ''}",
        )
        self.details_id_var.set(
            f"ID: {display_id(result['id'])}" if result.get("id") else "ID: —"
        )
        similarity = round(self._score(result, self.item.query) * 100)
        self.confidence_var.set(f"{similarity}% CONFIDENCE")
        genres = result.get("genres") or []
        if genres and isinstance(genres[0], dict):
            genres = [genre.get("name", "") for genre in genres]
        genre_text = ", ".join(str(name) for name in genres if name)
        self._genre_text = f"Genres: {genre_text or '—'}"
        if self.is_tv:
            creators = result.get("created_by") or []
            creator = ", ".join(
                person.get("name", "") for person in creators
                if person.get("name")
            )
            self._director_text = f"Creator: {creator or '—'}"
            credits = result.get("credits") or {}
        else:
            credits = result.get("credits") or {}
            directors = [
                person.get("name", "")
                for person in credits.get("crew", [])
                if person.get("job") == "Director" and person.get("name")
            ]
            self._director_text = (
                f"Director: {', '.join(directors) or '—'}"
            )
        cast = credits.get("cast") or []
        self._cast_names = [
            person.get("name", "")
            for person in cast[:5]
            if person.get("name")
        ]
        self._overview_text = (
            result.get("overview") or "No overview available."
        )
        self.overview_var.set(self._overview_text)
        self._resize_details_text()
        poster_path = result.get("poster_path")
        self._details_poster_path = poster_path
        self.details_poster.configure(
            image="",
            text="No\nposter" if not poster_path else "Loading…",
            width=11,
            height=7,
        )
        self.details_poster.image = None
        if poster_path:
            result_index = self.selected_index
            if result_index is not None:
                self._request_details_poster(result_index, poster_path)

        if self.is_tv:
            episode_title = result.get("episode_title")
            season = result.get(
                "_episode_season_number", self.item.season_number,
            )
            episode = result.get(
                "_episode_number", self.item.episode_number,
            )
            episode_end = result.get(
                "_episode_end_number", self.item.episode_end_number,
            )
            if (
                season is not None and episode is not None
            ):
                current = f"S{season:02d}E{episode:02d}"
                if isinstance(episode_end, int) and episode_end > episode:
                    current += f"-E{episode_end:02d}"
                if episode_title:
                    current += f" · {episode_title}"
            else:
                current = "Season and episode information unavailable."
            self.tv_info_var.set(current)
            if result.get("_episode_error"):
                self.search_status_var.set(result["_episode_error"])
            elif not episode_title:
                self.search_status_var.set("Loading episode details…")
            self._set_match_button_state(
                bool(
                    result.get("id")
                    and self.item.season_number is not None
                    and self.item.episode_number is not None
                    and not result.get("_episode_mapping_pending")
                    and not result.get("_absolute_unmapped")
                ),
            )
        else:
            self._set_match_button_state(bool(result.get("id")))

    def _clear_details(self):
        self.details_title_var.set("Select a result")
        self.details_id_var.set("")
        self.confidence_var.set("")
        self.genres_var.set("")
        self.director_var.set("")
        self.cast_var.set("")
        self._genre_text = ""
        self._director_text = ""
        self._cast_names = []
        self._overview_text = ""
        self.overview_var.set("")
        self.tv_info_var.set("")
        self.details_poster.configure(
            image="", text="No\nposter", width=11, height=7,
        )
        self.details_poster.image = None
        if hasattr(self, "update_button"):
            self.update_button.configure(state="disabled")

    def _resize_details_text(self, _event=None):
        content_width = self.details_content.winfo_width()
        poster_width = max(
            DETAIL_POSTER_SIZE[0], self.details_poster.winfo_reqwidth(),
        )
        side_width = max(
            140, content_width - 20 - poster_width - 9,
        )
        full_width = max(190, content_width - 24)
        for label in (
            self.details_title_label, self.genres_label,
            self.director_label, self.cast_label,
        ):
            label.configure(wraplength=side_width)
        self.genres_var.set(self._fit_detail_line(self._genre_text, side_width))
        self.director_var.set(
            self._fit_detail_line(self._director_text, side_width),
        )
        self.cast_var.set(self._format_cast(self._cast_names, side_width))
        self.overview_label.configure(wraplength=full_width)
        self.overview_var.set(self._fit_overview(self._overview_text, full_width))
        self.tv_info_label.configure(wraplength=full_width)

    def _fit_overview(self, text, width, max_lines=3):
        if not text:
            return ""
        words = text.split()
        lines = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and self._details_font.measure(candidate) > width:
                lines.append(current)
                current = word
                if len(lines) == max_lines:
                    last_line = lines[-1]
                    while last_line and self._details_font.measure(
                        f"{last_line.rstrip()}...",
                    ) > width:
                        last_line = last_line[:-1]
                    lines[-1] = f"{last_line.rstrip()}..."
                    return "\n".join(lines)
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines)

    def _fit_detail_line(self, text, width):
        if not text or self._details_font.measure(text) <= width:
            return text
        shortened = text
        while shortened and self._details_font.measure(
            f"{shortened.rstrip()}…",
        ) > width:
            shortened = shortened[:-1]
        return f"{shortened.rstrip()}…" if shortened else "…"

    def _format_cast(self, names, width):
        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            return "Cast: —"
        lines = ["Cast:"]
        included = 0
        overflow = False
        for name in names:
            candidate = f"{lines[0]}, {name}" if lines[0] != "Cast:" else (
                f"Cast: {name}"
            )
            if self._details_font.measure(candidate) <= width:
                lines[0] = candidate
                included += 1
                continue
            if len(lines) == 1:
                lines.append(self._fit_detail_line(name, width))
                included += 1
                overflow = lines[1] != name
                if overflow:
                    break
                continue
            candidate = f"{lines[1]}, {name}"
            if self._details_font.measure(candidate) <= width:
                lines[1] = candidate
                included += 1
                continue
            overflow = True
            break
        if overflow or included < len(names):
            suffix = lines[-1].rstrip(", ") + "…"
            lines[-1] = self._fit_detail_line(suffix, width)
        return "\n".join(lines[:2])
