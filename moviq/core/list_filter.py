"""Filtering helpers for the main file list."""
import re


_SEARCH_SEPARATOR = re.compile(r"[^\w]+", re.UNICODE)


def _search_words(value):
    normalized = _SEARCH_SEPARATOR.sub(" ", str(value or "").casefold())
    return normalized


def is_error_item(item):
    return item.status == "error" or bool(getattr(item, "error", ""))


def matches_list_filter(
    item, status_filter=None, media_mode=None, query="", new_name="",
    original_name="",
):
    media_type = getattr(item, "media_type", None)
    if media_mode and media_type is not None and media_type != media_mode:
        return False

    status = getattr(item, "status", None)
    if status_filter not in (None, "all"):
        if status is None:
            if status_filter == "error":
                return is_error_item(item)
            return False
        if status_filter == "ready":
            if is_error_item(item) or status not in ("auto", "manual"):
                return False
        elif status_filter == "done":
            if is_error_item(item) or status not in ("done", "no_change"):
                return False
        elif status_filter == "review":
            if status != "review" or is_error_item(item):
                return False
        elif status_filter == "nomatch":
            if is_error_item(item):
                return False
            if status != "nomatch":
                return False
        elif status_filter == "error":
            if not is_error_item(item):
                return False
        elif status != status_filter:
            return False

    query_words = _search_words(query).split()
    if not query_words:
        return True
    return any(
        all(word in candidate for word in query_words)
        for candidate in (
            _search_words(original_name),
            _search_words(new_name),
        )
    )
