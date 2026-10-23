from .matching import NO_MATCH_FLOOR, _is_generic_title
from .matching import is_confident, is_year_like_title, rank, score
from .matching import to_choice, to_tv_choice, year_of
from .naming import norm, similarity
from .show_resolver import ShowState


def match_movie_items(tmdb, items, threshold, search_query):
    for item in items:
        item.match_method = "title only"
    if _is_generic_title(items[0].query):
        return [("nomatch", None, "") for _item in items]
    search_results = tmdb.search(search_query, items[0].year)
    results = []
    for item in items:
        ranked = rank(search_results, item.query, item.year)
        if not ranked or score(ranked[0], item.query) < NO_MATCH_FLOOR:
            results.append(("nomatch", None, ""))
            continue
        confident = is_confident(
            ranked, item.query, item.year, threshold,
        )
        year_title = not item.year and is_year_like_title(item.query)
        match = ranked[0]
        offline = bool(match.get("offline"))
        status = (
            "auto" if confident and not year_title and not offline
            else "review"
        )
        choice = to_choice(match)
        match_year = year_of(match)
        if offline:
            review_reason = "offline"
        elif year_title:
            review_reason = "year_title"
        elif item.year and match_year != item.year:
            review_reason = "year"
        else:
            candidate_scores = [
                score(candidate, item.query) for candidate in ranked
            ]
            best_score = score(match, item.query)
            if sum(
                candidate_score >= max(threshold, best_score - 0.05)
                for candidate_score in candidate_scores
            ) > 1:
                review_reason = "similar"
            else:
                review_reason = "partial"
        results.append((status, choice, "", review_reason))
    return results


def match_tv_items(
    tmdb, show_resolver, items, operation_cache, threshold, review_reason,
):
    def get_season(show_id, season_number):
        return tmdb.season(show_id, season_number) or {}

    def get_season_episode(show_id, season_number, episode_number):
        season = get_season(show_id, season_number)
        for episode in season.get("episodes") or []:
            if episode.get("episode_number") == episode_number:
                episode = dict(episode)
                episode.setdefault("season_number", season_number)
                return episode
        return None

    def cumulative_episode(show_id, number):
        """Map an absolute number by adding up the season lengths."""
        details = tmdb.tv(show_id) or {}
        seasons = sorted(
            (
                season for season in details.get("seasons") or []
                if isinstance(season.get("season_number"), int)
                and season["season_number"] >= 1
            ),
            key=lambda season: season["season_number"],
        )
        remaining = number
        for season in seasons:
            count = season.get("episode_count") or 0
            if 0 < remaining <= count:
                return get_season_episode(
                    show_id, season["season_number"], remaining,
                )
            remaining -= count
        return None

    results = []
    for item in items:
        item.match_method = None
        resolved = show_resolver.resolve_show(
            item.query, item.year, operation_cache,
        )
        item.show_result = resolved
        if resolved.state == ShowState.NOT_FOUND:
            results.append(("nomatch", None, ""))
            continue
        if resolved.state == ShowState.ERROR:
            results.append((
                "error", None,
                resolved.error or "TV show lookup failed.",
            ))
            continue
        show = dict(resolved.candidate or {})
        show.update({
            "id": resolved.tmdb_id,
            "name": resolved.name,
            "year": resolved.year,
        })
        show_id = resolved.tmdb_id
        season_number = item.season_number
        episode_numbers = item.episode_numbers
        if item.episode_number is None and item.episode_title:
            item.match_method = "episode title"
            show_details = tmdb.tv(show_id) or {}
            title_matches = []
            for season in show_details.get("seasons") or []:
                season_number_for_match = season.get("season_number")
                if not isinstance(season_number_for_match, int):
                    continue
                season_data = get_season(
                    show_id, season_number_for_match,
                )
                for episode in season_data.get("episodes") or []:
                    episode_name = episode.get("name")
                    code = getattr(item, "production_code", None)
                    code_matches = bool(
                        code
                        and str(
                            episode.get("production_code") or "",
                        ).upper() == code
                    )
                    if (
                        episode_name
                        and (
                            norm(episode_name) == norm(item.episode_title)
                            or similarity(
                                episode_name, item.episode_title,
                            ) >= 0.92
                            or code_matches
                        )
                    ):
                        episode = dict(episode)
                        episode.setdefault(
                            "season_number", season_number_for_match,
                        )
                        if code_matches:
                            episode["_matched_by_code"] = True
                        title_matches.append(episode)
            if len(title_matches) != 1:
                results.append((
                    "review", None, "",
                    "episode_title",
                ))
                continue
            matched_episode = title_matches[0]
            if matched_episode.get("_matched_by_code"):
                item.match_method = "production code"
            season_number = matched_episode["season_number"]
            episode_numbers = (matched_episode["episode_number"],)
            episodes = [matched_episode]
            mapped_numbers = list(episode_numbers)
            episode_title = matched_episode.get("name")
            choice = to_tv_choice(
                show, season_number, mapped_numbers[0], episode_title,
            )
            confident = resolved.confidence >= threshold
            status = "auto" if confident else "review"
            reason = review_reason(
                choice, item.query, item.year, [choice], threshold, True,
            )
            if show.get("offline"):
                status, reason = "review", "offline"
            results.append((status, choice, "", reason))
            continue
        absolute_needed = not item.season_was_explicit
        if not absolute_needed:
            season_data = get_season(
                show_id, season_number,
            )
            item.season_episode_count = len(
                season_data.get("episodes") or []
            )
            absolute_needed = (
                item.episode_number > item.season_episode_count
            )
        item.match_method = (
            "absolute number" if absolute_needed
            else "season and episode in file name"
        )
        episodes = []
        if absolute_needed:
            episodes = [
                tmdb.absolute_episode(show_id, number)
                or (
                    None if item.season_was_explicit
                    else cumulative_episode(show_id, number)
                )
                for number in episode_numbers
            ]
            mapped = (
                all(episodes)
                and len({
                    episode.get("season_number")
                    for episode in episodes
                }) == 1
                and all(
                    isinstance(
                        episode.get("episode_number"), int,
                    )
                    for episode in episodes
                )
                and all(
                    episodes[index]["episode_number"]
                    == episodes[0]["episode_number"] + index
                    for index in range(len(episodes))
                )
            )
            if not mapped:
                choice = to_tv_choice(
                    show, None, None,
                )
                results.append((
                    "review", choice, "",
                    "episode_missing" if item.season_was_explicit
                    else "absolute",
                ))
                continue
            season_number = episodes[0]["season_number"]
            mapped_numbers = [
                episode["episode_number"]
                for episode in episodes
            ]
        else:
            mapped_numbers = list(episode_numbers)
            episodes = [
                get_season_episode(
                    show_id, season_number, number,
                )
                for number in mapped_numbers
            ]
        if not all(episodes):
            episode_title = None
        else:
            episode_titles = [
                episode.get("name") or ""
                for episode in episodes
            ]
            episode_title = (
                " & ".join(episode_titles)
                if all(episode_titles) else None
            )
        episode_number = mapped_numbers[0]
        episode_end_number = (
            mapped_numbers[-1]
            if len(mapped_numbers) > 1 else None
        )
        choice = to_tv_choice(
            show, season_number, episode_number,
            episode_title, episode_end_number,
        )
        confident = resolved.confidence >= threshold
        status = (
            "auto" if confident and episode_title else "review"
        )
        error = "" if episode_title else (
            "TMDB episode title unavailable; review this "
            "episode before renaming."
        )
        if not confident:
            error = ""
        reason = review_reason(
            choice, item.query, item.year, [choice], threshold, True,
        )
        if show.get("offline"):
            status, reason, error = "review", "offline", ""
        results.append((
            status, choice, error, reason,
        ))
    return results
