from .matching import is_confident, rank, score, to_choice
from .matching import to_tv_choice
from .show_resolver import ShowState


def match_movie_items(tmdb, items, threshold, search_query):
    search_results = tmdb.search(search_query, items[0].year)
    results = []
    for item in items:
        ranked = rank(search_results, item.query)
        if not ranked:
            results.append(("nomatch", None, ""))
            continue
        confident = is_confident(
            ranked, item.query, item.year, threshold,
        )
        status = "auto" if confident else "review"
        match = ranked[0]
        choice = to_choice(match)
        match_year = match.get("year")
        if item.year and match_year != item.year:
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

    results = []
    for item in items:
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
        absolute_needed = not item.season_was_explicit
        if not absolute_needed:
            season_data = get_season(
                show_id, season_number,
            )
            absolute_needed = (
                item.episode_number
                > len(season_data.get("episodes") or [])
            )
        episodes = []
        if absolute_needed:
            episodes = [
                tmdb.absolute_episode(show_id, number)
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
                    "review", choice, "", "absolute",
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
        results.append((
            status, choice, error, reason,
        ))
    return results
