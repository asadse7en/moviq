from .matching import is_confident, rank, score, to_choice


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
