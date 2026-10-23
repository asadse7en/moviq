import re

from .providers import ids as provider_ids

MOVIE_FORMATS = (
    ("title_year", "Title and year", "{title} ({year})", "{title} ({year})"),
    ("year_first", "Year first", "{year} - {title}", "{title}"),
    (
        "plex_id", "Plex ID in folder", "{title} ({year}) {tmdb-{id}}",
        "{title} ({year})",
    ),
    ("short_file", "Short file name", "{title} ({year})", "{title}"),
    (
        "square_brackets", "Square brackets", "{title} [{year}]",
        "{title} [{year}]",
    ),
    ("spaces_only", "Spaces only", "{title} {year}", "{title} {year}"),
    ("underscores", "Underscores", "{title}_{year}", "{title}_{year}"),
)
TV_FORMATS = (
    (
        "standard_s01e01", "Standard [S01E01]", "{title} ({year})",
        "Season {season:00}",
        "{title} ({year}) - S{season:00}E{episode:00} - {episode_title}",
        "S01E01",
    ),
    (
        "no_year_s01e01", "No year [S01E01]", "{title}",
        "Season {season:00}",
        "{title} - S{season:00}E{episode:00} - {episode_title}",
        "S01E01",
    ),
    (
        "short_s01e01", "Short file name [S01E01]", "{title}",
        "Season {season:00}", "S{season:00}E{episode:00} - {episode_title}",
        "S01E01",
    ),
    (
        "no_dashes_s01e01", "No dashes [S01E01]", "{title}",
        "Season {season:00}",
        "{title} S{season:00}E{episode:00} {episode_title}", "S01E01",
    ),
    (
        "standard_1x01", "Standard [1x01]", "{title}", "Season {season}",
        "{title} - {season}x{episode:00} - {episode_title}", "1x01",
    ),
    (
        "no_title_1x01", "No episode title [1x01]", "{title}", "Season {season}",
        "{title} - {season}x{episode:00}", "1x01",
    ),
    (
        "short_1x01", "Short file name [1x01]", "{title}", "Season {season}",
        "{season}x{episode:00} - {episode_title}", "1x01",
    ),
)

MOVIE_DEFAULT_ID = "title_year"
TV_DEFAULT_ID = "no_year_s01e01"
CUSTOM_ID = "custom"

_TOKEN_RE = re.compile(
    r"\{(title|year|id|season(?::00|:02d)?|episode(?::00|:02d)?|episode_title)\}",
)
_INVALID_COMPONENT_CHARS = re.compile(r'[\\/:*?"<>|]')
_WINDOWS_RESERVED_NAME_RE = re.compile(
    r"(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])",
)


def _avoid_windows_reserved_name(name):
    first_part = name.split(".", 1)[0].rstrip(" ")
    if _WINDOWS_RESERVED_NAME_RE.fullmatch(first_part):
        return (
            name[:len(first_part)] + "_" + name[len(first_part):]
        )
    return name


def _provider_aware(pattern, values):
    """Make {tmdb-{id}} tags honest for providers with standard id tags."""
    composite = values.get("id") if isinstance(values, dict) else None
    if not isinstance(composite, int) or composite < provider_ids.OFFSET:
        return pattern, values
    tag = provider_ids.external_tag(composite)
    if tag:
        label, value = tag
        pattern = re.sub(
            r"tmdb(id)?-\{id\}",
            lambda match: f"{label}{match.group(1) or ''}-{{id}}",
            pattern,
        )
    else:
        value = str(provider_ids.native_id(composite))
        pattern = re.sub(r"\s*[\[{]tmdb(?:id)?-\{id\}[\]}]", "", pattern)
    return pattern, dict(values, id=value)


def format_component(pattern, values):
    """Format known tokens only and sanitize one filesystem path component."""
    if not isinstance(pattern, str):
        return ""
    pattern, values = _provider_aware(pattern, values)

    def replace(match):
        token = match.group(1)
        if token.endswith((":00", ":02d")):
            name = token.split(":", 1)[0]
            value = _value_for(name, values)
            try:
                return f"{int(value):02d}"
            except (TypeError, ValueError, OverflowError):
                return ""
        return str(_value_for(token, values))

    result = _TOKEN_RE.sub(replace, pattern)
    result = _INVALID_COMPONENT_CHARS.sub("", result)
    result = re.sub(r" {2,}", " ", result)
    return _avoid_windows_reserved_name(result.strip().rstrip(". "))


def _value_for(token, values):
    aliases = {
        "season": ("season", "season_number"),
        "episode": ("episode", "episode_number"),
    }
    for key in aliases.get(token, (token,)):
        value = values.get(key)
        if value is not None:
            return value
    return ""


def format_pattern(pattern, values, fallback=""):
    result = format_component(pattern, values)
    return result or format_component(fallback, values)
