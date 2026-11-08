from .matching import NO_MATCH_FLOOR, _is_generic_title, _normalized_match_title
from .matching import is_confident, is_year_like_title, rank, score
from .matching import to_choice, to_tv_choice, year_of
from .naming import norm, similarity
from .providers.ids import provider_of
from .show_resolver import ShowResolver, ShowState
from .. import trace


def _append_result(results, item, result, on_item_complete):
    results.append(result)
    trace.trace_end(lambda: str(item.path), result)
    if on_item_complete is not None:
        on_item_complete(item)


def match_movie_items(
    tmdb, items, threshold, search_query, on_item_complete=None,
):
    original_items = list(items)
    skipped = {}
    matching_items = []
    skipped_results = []
    for item in original_items:
        if getattr(item, "kind", None) in ("extra", "special"):
            result = ("nomatch", None, "")
            skipped[item] = result
            _append_result(
                skipped_results, item, result, on_item_complete,
            )
        else:
            matching_items.append(item)
    if not matching_items:
        return [skipped[item] for item in original_items]
    items = matching_items

    def restore_order(results):
        by_item = dict(skipped)
        by_item.update(zip(items, results))
        return [by_item[item] for item in original_items]

    for item in items:
        item.match_method = "title only"
    trace.trace_stage(
        lambda: [str(item.path) for item in items],
        "provider_query", search_query,
    )
    search_results = tmdb.search(search_query, items[0].year)
    if _is_generic_title(items[0].query):
        results = []
        for item in items:
            ranked = rank(search_results, item.query, item.year)
            if ranked:
                lead = ranked[0]
                original_title = (lead.get("original_title") or "").strip()
                normalized_query = _normalized_match_title(item.query)
                if (
                    original_title
                    and _normalized_match_title(original_title) == normalized_query
                    and _normalized_match_title(lead.get("title") or "")
                    != normalized_query
                ):
                    _append_result(
                        results, item, ("review", None, ""), on_item_complete,
                    )
                    continue
            _append_result(
                results, item, ("nomatch", None, ""), on_item_complete,
            )
        return restore_order(results)
    results = []
    for item in items:
        ranked = rank(search_results, item.query, item.year)
        trace.trace_stage(
            lambda: str(item.path), "top_candidates",
            lambda: [
                (candidate, score(candidate, item.query))
                for candidate in ranked[:5]
            ],
        )
        if ranked:
            lead = ranked[0]
            original_title = (lead.get("original_title") or "").strip()
            normalized_query = _normalized_match_title(item.query)
            if (
                original_title
                and _normalized_match_title(original_title) == normalized_query
                and _normalized_match_title(lead.get("title") or "")
                != normalized_query
            ):
                _append_result(
                    results, item, ("review", None, ""), on_item_complete,
                )
                continue
        if not ranked or score(ranked[0], item.query) < NO_MATCH_FLOOR:
            _append_result(
                results, item, ("nomatch", None, ""), on_item_complete,
            )
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
        _append_result(
            results, item, (status, choice, "", review_reason),
            on_item_complete,
        )
    return restore_order(results)


def match_tv_items(
    tmdb, show_resolver, items, operation_cache, threshold, review_reason,
    on_item_complete=None,
):
    def get_season(show_id, season_number, provider_season=None):
        if provider_season is not None:
            season_number = provider_season
        return tmdb.season(show_id, season_number) or {}

    def get_season_episode(
        show_id, season_number, episode_number, provider_season=None,
    ):
        season = get_season(show_id, season_number, provider_season)
        for episode in season.get("episodes") or []:
            if episode.get("episode_number") == episode_number:
                episode = dict(episode)
                episode["season_number"] = season_number
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
        for season_index, season in enumerate(seasons, start=1):
            count = season.get("episode_count") or 0
            if 0 < remaining <= count:
                return get_season_episode(
                    show_id, season_index, remaining,
                    season["season_number"],
                )
            remaining -= count
        return None

    results = []
    for item in items:
        item.match_method = None
        if getattr(item, "kind", None) in ("extra", "special"):
            _append_result(
                results, item, ("nomatch", None, ""), on_item_complete,
            )
            continue
        trace.trace_stage(lambda: str(item.path), "show_query", item.query)
        if isinstance(show_resolver, ShowResolver):
            resolved = show_resolver.resolve_show(
                item.query, item.year, operation_cache,
                season_hint=getattr(item, "season_hint", None),
                episode_number=item.episode_number,
                season_number=item.season_number,
            )
        else:
            resolved = show_resolver.resolve_show(
                item.query, item.year, operation_cache,
            )
        item.show_result = resolved
        if resolved.state == ShowState.NOT_FOUND:
            _append_result(
                results, item, ("nomatch", None, ""), on_item_complete,
            )
            continue
        if resolved.state == ShowState.ERROR:
            _append_result(
                results, item, (
                    "error", None,
                    resolved.error or "TV show lookup failed.",
                ), on_item_complete,
            )
            continue
        show = dict(resolved.candidate or {})
        show.update({
            "id": resolved.tmdb_id,
            "name": resolved.name,
            "year": resolved.year,
        })
        show_id = resolved.tmdb_id
        is_anilist = provider_of(show_id) == "anilist"
        provider_season_number = 1 if is_anilist else None
        anime_season_index = show.get("_anime_season_index", 1)
        season_number = (
            anime_season_index if is_anilist else item.season_number
        )
        season_mismatch = bool(
            is_anilist
            and (
                show.get("_anime_season_mismatch")
                or (
                    isinstance(item.season_hint, int)
                    and item.season_hint != anime_season_index
                )
            )
        )
        episode_numbers = item.episode_numbers
        if item.air_date and item.episode_number is None:
            item.match_method = "air date"
            trace.trace_stage(
                lambda: str(item.path), "episode_lookup",
                {"air_date": item.air_date},
            )
            episodes = tmdb.episodes_by_date(show_id, item.air_date) or []
            episodes = [
                episode for episode in episodes
                if (episode.get("air_date") or "")[:10] == item.air_date
                and isinstance(episode.get("season_number"), int)
                and isinstance(episode.get("episode_number"), int)
            ]
            if len(episodes) != 1:
                _append_result(
                    results, item, (
                        "review", to_tv_choice(show, None, None), "",
                        "air_date",
                    ), on_item_complete,
                )
                continue
            episode = episodes[0]
            season_number = episode["season_number"]
            episode_number = episode["episode_number"]
            episode_title = episode.get("name") or None
            item.season_number = season_number
            item.episode_number = episode_number
            item.episode_numbers = (episode_number,)
            item.episode_end_number = None
            item.episode_title = episode_title
            item.season_was_explicit = True
            choice = to_tv_choice(
                show, season_number, episode_number, episode_title,
            )
            status = (
                "auto"
                if resolved.confidence >= threshold
                and episode_title and not show.get("offline")
                and not season_mismatch and not resolved.ambiguous
                else "review"
            )
            reason = review_reason(
                choice, item.query, item.year, [choice], threshold, True,
            )
            if show.get("offline"):
                reason = "offline"
            _append_result(
                results, item, (status, choice, "", reason),
                on_item_complete,
            )
            continue
        if item.episode_number is None and item.episode_title:
            item.match_method = "episode title"
            trace.trace_stage(
                lambda: str(item.path), "episode_lookup",
                {"episode_title": item.episode_title},
            )
            show_details = tmdb.tv(show_id) or {}
            title_matches = []
            for season in show_details.get("seasons") or []:
                season_number_for_match = season.get("season_number")
                if not isinstance(season_number_for_match, int):
                    continue
                season_data = get_season(
                    show_id, season_number_for_match,
                    provider_season_number,
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
                _append_result(
                    results, item, (
                        "review", None, "", "episode_title",
                    ), on_item_complete,
                )
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
            status = (
                "auto" if confident and not season_mismatch
                and not resolved.ambiguous else "review"
            )
            reason = review_reason(
                choice, item.query, item.year, [choice], threshold, True,
            )
            if show.get("offline"):
                status, reason = "review", "offline"
            _append_result(
                results, item, (status, choice, "", reason),
                on_item_complete,
            )
            continue
        compact_number = getattr(item, "compact_episode_number", None)
        compact_handled = False
        if compact_number is not None:
            item.match_method = "compact season and episode"
            trace.trace_stage(
                lambda: str(item.path), "episode_lookup",
                {
                    "season": season_number,
                    "episode": item.episode_number,
                    "compact_number": compact_number,
                },
            )
            show_details = tmdb.tv(show_id) or {}
            max_season = max(
                (
                    season.get("season_number", 0)
                    for season in show_details.get("seasons") or []
                    if isinstance(season.get("season_number"), int)
                ),
                default=0,
            )
            season_data = (
                get_season(
                    show_id, season_number, provider_season_number,
                )
                if (
                    season_number is not None
                    and (
                        provider_season_number is not None
                        or season_number <= max_season
                    )
                )
                else {}
            )
            item.season_episode_count = len(
                season_data.get("episodes") or [],
            )
            matched_compact = next(
                (
                    dict(episode)
                    for episode in season_data.get("episodes") or []
                    if episode.get("episode_number")
                    == item.episode_number
                ),
                None,
            )
            if matched_compact:
                matched_compact.setdefault(
                    "season_number", season_number,
                )
            if matched_compact:
                episodes = [matched_compact]
                mapped_numbers = [item.episode_number]
                compact_handled = True
            else:
                item.match_method = "absolute number"
                absolute_episode = (
                    cumulative_episode(show_id, compact_number)
                    or tmdb.absolute_episode(show_id, compact_number)
                )
                if (
                    absolute_episode
                    and isinstance(
                        absolute_episode.get("season_number"), int,
                    )
                    and isinstance(
                        absolute_episode.get("episode_number"), int,
                    )
                ):
                    episodes = [absolute_episode]
                    season_number = absolute_episode["season_number"]
                    mapped_numbers = [
                        absolute_episode["episode_number"],
                    ]
                    compact_handled = True
                else:
                    choice = to_tv_choice(
                        show, item.season_number, item.episode_number,
                    )
                    _append_result(
                        results, item, (
                            "review", choice, "", "episode_missing",
                        ), on_item_complete,
                    )
                    continue
        if compact_handled:
            absolute_needed = False
        else:
            absolute_needed = not item.season_was_explicit
        if not compact_handled and not absolute_needed:
            trace.trace_stage(
                lambda: str(item.path), "episode_lookup",
                {"season": season_number, "episodes": episode_numbers},
            )
            season_data = get_season(
                show_id, season_number, provider_season_number,
            )
            item.season_episode_count = len(
                season_data.get("episodes") or []
            )
            if (
                item.season_episode_count
                and item.episode_number > item.season_episode_count
            ):
                item.match_method = "season and episode in file name"
                choice = to_tv_choice(
                    show, item.season_number, item.episode_number,
                )
                _append_result(
                    results, item, (
                        "review", choice, "", "episode_missing",
                    ), on_item_complete,
                )
                continue
        if not compact_handled:
            item.match_method = (
                "absolute number" if absolute_needed
                else "season and episode in file name"
            )
        if absolute_needed:
            trace.trace_stage(
                lambda: str(item.path), "episode_lookup",
                {"absolute_episodes": episode_numbers},
            )
        if not compact_handled:
            episodes = []
        if not compact_handled and absolute_needed:
            episodes = [
                cumulative_episode(show_id, number)
                or (
                    tmdb.absolute_episode(show_id, number)
                    if not item.season_was_explicit else None
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
                    show,
                    item.season_number if item.season_was_explicit else None,
                    item.episode_number if item.season_was_explicit else None,
                )
                _append_result(
                    results, item, (
                        "review", choice, "",
                        "episode_missing" if item.season_was_explicit
                        else "absolute",
                    ), on_item_complete,
                )
                continue
            season_number = episodes[0]["season_number"]
            mapped_numbers = [
                episode["episode_number"]
                for episode in episodes
            ]
        elif not compact_handled:
            mapped_numbers = list(episode_numbers)
            episodes = [
                get_season_episode(
                    show_id, season_number, number,
                    provider_season_number,
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
            "auto" if confident and episode_title
            and not season_mismatch and not resolved.ambiguous else "review"
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
        _append_result(
            results, item, (status, choice, error, reason),
            on_item_complete,
        )
    return results
