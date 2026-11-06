import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from .formatting import _avoid_windows_reserved_name
from .providers import ids as provider_ids


YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|2100)(?!\d)")
AIR_DATE_RE = re.compile(
    r"(?<![a-z0-9])(?:\d{4}[-.]\d{2}[-.]\d{2}|\d{8}|"
    r"\d{2}[.-]\d{2}[.-]\d{4})(?![a-z0-9])",
    re.IGNORECASE,
)
FILE_NUMBER_RE = re.compile(r"^\s*0\d{0,2}\s+-\s+")
TV_EPISODE_RE = re.compile(
    r"(?i)(?<![a-z0-9])S(?P<season>\d{1,2})[x ._-]*E(?P<episode>\d{1,4})"
    r"(?:(?:-?E|-)(?P<episode_end>\d{1,4}))?(?!\d)"
    r"|(?<!\d)(?P<season_x>\d{1,2})[xX](?P<episode_x>\d{1,4})"
    r"(?:-(?P<episode_end_x>\d{1,4}))?(?!\d)"
    r"|(?<![a-z0-9])Season[ ._-]*(?P<season_word>\d{1,2})"
    r"[ ._-]*Episode[ ._-]*(?P<episode_word>\d{1,4})(?!\d)"
)
CONTEXT_EPISODE_RE = re.compile(
    r"(?i)^(?:[a-z]{1,8}[ ._-]*)?(?:ep(?:isode)?|e)[ ._-]*0*"
    r"(?P<episode>\d{1,4})$"
)
COMPACT_EPISODE_RE = re.compile(
    r"(?i)^[a-z]{1,4}[ ._-]*0*(?P<episode>\d{1,4})$"
)
NUMBERED_EPISODE_RE = re.compile(r"^0*(?P<episode>\d{1,4})$")
CONTEXT_NUMBERED_TITLE_RE = re.compile(
    r"^\s*(?P<episode>\d{1,3})(?:\s+-\s+|\.\s+|\s+)(?P<title>.+?)\s*$"
)
TRAILING_EPISODE_RE = re.compile(
    r"(?:^|[\s._-])(?P<episode>\d{1,4})$",
)
ANIME_EPISODE_RE = re.compile(
    r"(?i)(?<!\d\.)(?<![a-z0-9])(?P<episode>\d{1,4})"
    r"(?:v\d+)?(?:\s+(?:END|FINAL))?\s*$",
)
TV_NAME_MARKER_RE = re.compile(
    r"(?i)(?<![a-z0-9])"
    r"(?:S(?P<season>\d{1,2})[x ._-]*E(?P<episode>\d{1,4})"
    r"(?:(?:-?E|-)(?P<episode_end>\d{1,4}))?"
    r"|(?P<season_x>\d{1,2})[xX](?P<episode_x>\d{1,4})"
    r"(?:-(?P<episode_end_x>\d{1,4}))?"
    r"|Season[ ._-]*(?P<season_word>\d{1,2})"
    r"[ ._-]*Episode[ ._-]*(?P<episode_word>\d{1,4})"
    r"|(?:Ep(?:isode)?[ ._-]*|E[ ._-]*)0*"
    r"(?P<bare_episode>\d{1,4}))(?!\d)"
)
TV_SEASON_MARKER_RE = re.compile(
    r"(?i)(?<![a-z0-9])(?:season[ ._-]*|s)(\d{1,2})(?!\d)",
)
CJK_EPISODE_RE = re.compile(
    r"第\s*(?P<episode_cjk>\d{1,4})\s*[話话集回]"
    r"|(?<!\d)(?P<episode_jp>\d{1,4})\s*話"
    r"|(?<!\d)(?P<episode_ko>\d{1,4})\s*[화회]"
)
_ANIME_EXTRA_RE = re.compile(
    r"(?i)(?:^|[\s._-])(?P<token>"
    r"NCOP\s*\d*|NCED\s*\d*|OP\s*\d*|ED\s*\d*|"
    r"Web\s+Preview|Picture\s+Drama|"
    r"PV|CM|Bonus|Extra|Interview|Making(?:\s+Of)?|Menu|Preview|"
    r"Recap|Teaser|Trailer|SP\s*\d*|Specials?\s*\d*|OVA\s*\d*|"
    r"Ep(?:isode)?\s*0+)\s*$"
)
_ANIME_SEASON_HINT_RE = re.compile(
    r"(?ix)(?:^|[\s._-])"
    r"(?:(?P<ordinal>\d{1,3}(?:st|nd|rd|th)?)\s+season"
    r"|season\s*(?P<season>\d{1,3})"
    r"|s\s*(?P<s>\d{1,3})"
    r"|part\s*(?P<part>\d{1,3})"
    r"|cour\s*(?P<cour>\d{1,3})"
    r"|(?P<final>final)\s+season"
    r"|(?P<roman>ii|iii|iv|v))\s*$"
)
OCR_MARKER_RE = re.compile(
    r"(?<![A-Za-z0-9])S([0-9OIl]{2})[ ._-]?E([0-9OIl]{2,3})(?![A-Za-z0-9])"
)
PRODUCTION_CODE_RE = re.compile(
    r"^(?P<code>\d[A-Za-z]{2,4}\d{2,3})(?:\s*[-:]\s*|\s+)(?P<title>.+)$"
)
SPECIALS_DIR_RE = re.compile(r"(?i)^(?:specials?)$")
TV_GENERIC_TITLES = {
    "context", "episode", "episodes", "season", "seasons", "series", "show",
    "show folders", "the show",
    "show name", "example", "download", "downloads", "folder", "library",
    "media", "movies", "new folder", "shows", "tv", "tv shows", "video",
    "videos", "special", "specials",
}
TV_GENERIC_WORDS = {
    "the", "show", "name", "example", "episode", "episodes", "season",
    "series",
}
TV_RELEASE_TAG_RE = re.compile(
    r"(?i)\b(?:"
    r"480p|576p|720p|1080p|1080i|1440p|2160p|4k|uhd|"
    r"web[ ._-]?dl|web[ ._-]?rip|webrip|bluray|blu[ ._-]?ray|bdrip|brrip|"
    r"hdrip|dvdrip|hdtv|hdcam|hdts|telesync|"
    r"x264|x265|h[ ._-]?264|h[ ._-]?265|hevc|avc|av1|xvid|divx|"
    r"aac|ac3|eac3|dts|ddp5|dd5|atmos|"
    r"10bit|8bit|hdr10|hdr|dolby|remux|"
    r"proper|repack|extended|unrated|remastered|imax|"
    r"dual[ ._-]?audio|multiple[ ._-]?subtitles?|multi|hindi|english|esub|esubs|"
    r"french|german|spanish|italian|japanese|korean|chinese|russian|"
    r"portuguese|dubbed|subbed|"
    r"amzn|nf|dsnp|hmax|atvp|yify|yts|rarbg|ettv|galaxyrg|1337x|tgx|"
    r"eztv|ntb|ntg|group"
    r")\b"
)
JUNK_RE = re.compile(
    r"\b(480p|576p|720p|1080p|1080i|2160p|4k|uhd|bluray|blu-ray|bdrip|brrip|"
    r"webrip|web-dl|webdl|web|hdrip|dvdrip|dvdscr|hdtv|hdcam|cam|hdts|telesync|"
    r"x264|x265|h264|h265|hevc|avc|xvid|divx|aac|ac3|eac3|dts|ddp5|dd5|atmos|"
    r"10bit|8bit|hdr|hdr10|dolby|remux|proper|repack|extended|unrated|"
    r"directors cut|imax|dual audio|multi|hindi|english|esub|esubs|yify|yts|"
    r"rarbg|ettv|galaxyrg|amzn|nf|dsnp)\b",
    re.IGNORECASE,
)

NAMING = {
    "template": "{title} ({year})",
    "tv_template": "{title} - S{season:02d}E{episode:02d} - {episode_title}",
    "tag_id": False,
}


def _fix_ocr_markers(text):
    """Repair S01E01 markers typed with letters: SOI EOI -> S01E01."""
    table = str.maketrans({"O": "0", "I": "1", "l": "1"})

    def repair(match):
        season, episode = match.group(1), match.group(2)
        if season.isdigit() and episode.isdigit():
            return match.group(0)
        return "S%sE%s" % (season.translate(table), episode.translate(table))

    return OCR_MARKER_RE.sub(repair, text or "")


_OCR_YEAR_RE = re.compile(r"(?<![A-Za-z0-9])([12][09OoIl][0-9OoIl]{2})(?![A-Za-z0-9])")


def _fix_ocr_year(text):
    """Repair years typed with letters: 20O0 -> 2000 (only when it becomes 19xx/20xx)."""
    table = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1"})

    def repair(match):
        token = match.group(1)
        if token.isdigit():
            return token
        fixed = token.translate(table)
        return fixed if re.fullmatch(r"(?:19|20)\d{2}", fixed) else token

    return _OCR_YEAR_RE.sub(repair, text)


def _replace_title_separators(value):
    value = re.sub(r"(?<!\d)\.|(?<=\d)\.(?!\d)", " ", value or "")
    return value.replace("_", " ")


def _strip_parenthesized_release_tags(value):
    def replace(match):
        contents = match.group(1)
        if TV_RELEASE_TAG_RE.search(contents):
            return " "
        return match.group(0)

    return re.sub(r"\(([^()]*)\)", replace, value or "")


def _strip_tv_file_number(value):
    match = re.match(r"^\s*(\d{1,3})\s+-\s+", value or "")
    if not match:
        return value
    rest = value[match.end():]
    if match.group(1).startswith("0") or TV_NAME_MARKER_RE.search(rest):
        return rest
    return value


def parse_tv_season_hint(value):
    """Return a title and an optional anime sequel/season hint."""
    text = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", value or "")
    text = _strip_tv_file_number(text)
    release = TV_RELEASE_TAG_RE.search(text)
    if release:
        text = text[:release.start()]

    def suffix_hint(text):
        text = _replace_title_separators(text).strip(" ._-")
        match = _ANIME_SEASON_HINT_RE.search(text)
        if not match:
            return text, None
        hint = (
            int(re.sub(r"(?:st|nd|rd|th)$", "", match.group("ordinal"), flags=re.I))
            if match.group("ordinal") else
            int(
                match.group("season") or match.group("s")
                or match.group("part") or match.group("cour")
            )
            if any(match.group(name) for name in ("season", "s", "part", "cour"))
            else "final" if match.group("final")
            else {"ii": 2, "iii": 3, "iv": 4, "v": 5}[
                match.group("roman").casefold()
            ]
        )
        return text[:match.start()].strip(" ._-"), hint

    title, hint = suffix_hint(text)
    if hint is not None:
        return title, hint
    marker = TV_NAME_MARKER_RE.search(text)
    if marker:
        text = text[:marker.start()]
    else:
        cjk = CJK_EPISODE_RE.search(text)
        anime = ANIME_EPISODE_RE.search(text)
        trailing = TRAILING_EPISODE_RE.search(text)
        episode_marker = cjk or (
            anime if anime and not _is_year_like_episode(text, anime)
            else None
        ) or trailing
        if episode_marker:
            text = text[:episode_marker.start()]
    year = next(
        (match for match in YEAR_RE.finditer(text) if match.start() > 0),
        None,
    )
    if year:
        text = text[:year.start()]
    return suffix_hint(text)


def parse_tv_extra(stem):
    """Classify anime extras and specials without treating them as episodes."""
    text = _strip_parenthesized_release_tags(stem)
    text = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", text)
    marker = TV_NAME_MARKER_RE.search(text)
    if marker:
        episode = marker.groupdict().get("bare_episode")
        if episode is not None and int(episode) == 0:
            return "special"
    extra = _ANIME_EXTRA_RE.search(text)
    if not extra:
        return None
    token = extra.group("token").casefold()
    if token.startswith(("sp", "special", "ova", "ep")):
        return "special"
    return "extra"


def parse_tv_air_date(stem):
    """Return a valid air date from a supported filename date format."""
    stem = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", stem or "")
    for match in AIR_DATE_RE.finditer(stem):
        token = match.group()
        if "." in token or "-" in token:
            parts = re.split(r"[.-]", token)
        else:
            parts = (token[:4], token[4:6], token[6:])
        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)
        if not 1950 <= year <= 2100:
            continue
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return None


def parse_tv_compact_episode(stem):
    """Parse a trailing season/episode code such as 101 or 1001."""
    stem = _fix_ocr_markers(stem or "")
    date_match = AIR_DATE_RE.search(stem)
    if date_match and parse_tv_air_date(stem):
        stem = stem[:date_match.start()]
    release = TV_RELEASE_TAG_RE.search(stem)
    if release and release.start() > 0:
        stem = stem[:release.start()]
    match = re.search(r"(?<!\d)(?P<number>\d{3,4})$", stem.rstrip())
    if not match:
        return None
    number_text = match.group("number")
    number = int(number_text)
    if 1900 <= number <= 2100:
        return None
    season = int(number_text[:-2])
    episode = int(number_text[-2:])
    title = _clean_tv_title(stem[:match.start()])
    if season < 1 or episode < 1 or not _useful_tv_title(title):
        return None
    return season, episode, number


def parse_name(stem):
    """'The.Dark.Knight.2008.1080p.BluRay' -> ('The Dark Knight', 2008)"""
    stem = _fix_ocr_year(FILE_NUMBER_RE.sub("", stem))
    s = re.sub(r"[\[\{].*?[\]\}]", " ", stem)
    s = _replace_title_separators(s)
    year = None
    cands = [m for m in YEAR_RE.finditer(s) if m.start() > 0]
    if cands:
        year = int(cands[-1].group())
        s = s[:cands[-1].start()]
    junk = JUNK_RE.search(s)
    if junk and junk.start() > 0:
        s = s[:junk.start()]
    s = re.sub(r"[()\-]+", " ", s)
    s = re.sub(r"[\s.]+$", "", s)
    return re.sub(r"\s+", " ", s).strip(), year


def _clean_tv_title(value):
    value = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", value or "")
    value = FILE_NUMBER_RE.sub("", value)
    value = _replace_title_separators(value)
    value = re.sub(r"\s+-\s+", " ", value)
    value = re.sub(r"-[A-Z0-9]{2,}(?:[._-][A-Z0-9]+)*$", "", value)
    value = re.sub(r"[\s-]+$", "", value)
    year_match = next(
        (match for match in YEAR_RE.finditer(value) if match.start() > 0),
        None,
    )
    if year_match:
        value = value[:year_match.start()]
    value, _hint = parse_tv_season_hint(value)
    marker = TV_NAME_MARKER_RE.search(value)
    if marker:
        value = value[:marker.start()]
    season_marker = TV_SEASON_MARKER_RE.search(value)
    if season_marker:
        value = value[:season_marker.start()]
    release = TV_RELEASE_TAG_RE.search(value)
    if release:
        value = value[:release.start()]
    value = re.sub(r"[\s().#-]+$", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _useful_tv_title(value):
    normalized = re.sub(r"[^\w]+", " ", (value or "").casefold()).strip()
    return bool(
        normalized
        and (
            normalized.isdigit()
            or len(normalized.replace(" ", "")) >= 3
        )
        and normalized not in TV_GENERIC_TITLES
        and not set(normalized.split()).issubset(TV_GENERIC_WORDS)
        and not re.fullmatch(r"(?:season|series|s)\s*\d+", normalized)
    )


def _untagged_episode_parts(stem):
    stem = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", stem)
    stem = FILE_NUMBER_RE.sub("", stem)
    stem = _replace_title_separators(stem)
    release = TV_RELEASE_TAG_RE.search(stem)
    if release and release.start() > 0:
        stem = stem[:release.start()]
    parts = re.split(r"\s+-\s+", stem.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    show_title = _clean_tv_title(parts[0])
    code_match = PRODUCTION_CODE_RE.match(parts[1].strip())
    if code_match:
        parts[1] = code_match.group("title")
    episode_title = _clean_tv_title(parts[1])
    if not (
        _useful_tv_title(show_title)
        and _useful_tv_title(episode_title)
    ):
        return None
    return show_title, episode_title


def _bracketed_episode_title(stem):
    bracket = re.match(r"^\s*\[([^\]]+)\]\s+-\s+", stem or "")
    if not bracket or not TV_NAME_MARKER_RE.search(stem[bracket.end():]):
        return None
    title = _clean_tv_title(bracket.group(1))
    return title if _useful_tv_title(title) else None


def _is_decimal_trailing_number(stem, match):
    start = match.start("episode")
    return bool(
        start >= 2 and stem[start - 1] == "."
        and stem[start - 2].isdigit()
    )


def _is_year_like_episode(stem, match):
    number = match.group("episode")
    if not YEAR_RE.fullmatch(number):
        return False
    start = match.start("episode")
    return start == 0 or stem[start - 1] != "#"


def parse_tv_name(path, root=None):
    """Return (show title, year, season, episode) from an episode path."""
    path = Path(path)
    air_date = parse_tv_air_date(path.stem)
    source_stem = _strip_parenthesized_release_tags(path.stem)
    if air_date:
        date_match = next(
            match for match in AIR_DATE_RE.finditer(source_stem)
            if parse_tv_air_date(match.group())
        )
        source_stem = source_stem[:date_match.start()]
    bracket_title = _bracketed_episode_title(
        _fix_ocr_markers(source_stem),
    )
    extra_kind = parse_tv_extra(source_stem)
    stem = re.sub(
        r"\[[^\]]*\]|\{[^}]*\}", " ", _fix_ocr_markers(source_stem),
    )
    stem = _strip_tv_file_number(stem)
    if extra_kind:
        extra = _ANIME_EXTRA_RE.search(stem)
        if extra:
            stem = stem[:extra.start()]
    stem = _replace_title_separators(stem)

    release_match = TV_RELEASE_TAG_RE.search(stem)
    episode_stem = stem
    if release_match and release_match.start() > 0:
        episode_stem = stem[:release_match.start()]
    episode_stem = episode_stem.rstrip(" \t.([{_-")

    marker = TV_NAME_MARKER_RE.search(stem)
    compact_episode = parse_tv_compact_episode(source_stem)
    cjk_marker = None if marker else CJK_EPISODE_RE.search(episode_stem)
    anime_marker = (
        None if marker or cjk_marker or compact_episode
        else ANIME_EPISODE_RE.search(episode_stem)
    )
    if anime_marker and _is_year_like_episode(
        episode_stem, anime_marker,
    ):
        anime_marker = None
    number_match = NUMBERED_EPISODE_RE.fullmatch(episode_stem.strip())
    trailing_number = TRAILING_EPISODE_RE.search(episode_stem)
    year_match = next(
        (
            match for match in YEAR_RE.finditer(episode_stem)
            if match.start() > 0
        ),
        None,
    )

    boundary = len(stem)
    if marker:
        boundary = min(boundary, marker.start())
    elif cjk_marker:
        boundary = min(boundary, cjk_marker.start())
    elif anime_marker:
        boundary = min(boundary, anime_marker.start())
    if trailing_number and not number_match and not (
        year_match and year_match.start() == trailing_number.start(1)
    ):
        boundary = min(boundary, trailing_number.start(1))
    if year_match:
        boundary = min(boundary, year_match.start())
    if release_match and release_match.start() > 0:
        boundary = min(boundary, release_match.start())
    title = _clean_tv_title(stem[:boundary])
    if bracket_title:
        title = bracket_title
    elif number_match and not marker and not cjk_marker and not anime_marker:
        title = ""
    year = int(year_match.group()) if year_match else None

    season = episode = None
    if marker:
        groups = marker.groupdict()
        season_value = (
            groups.get("season") or groups.get("season_x")
            or groups.get("season_word")
        )
        episode_value = (
            groups.get("episode") or groups.get("episode_x")
            or groups.get("episode_word") or groups.get("bare_episode")
        )
        season = int(season_value) if season_value else None
        episode = int(episode_value) if episode_value else None
    elif cjk_marker:
        episode = int(
            cjk_marker.group("episode_cjk")
            or cjk_marker.group("episode_jp")
            or cjk_marker.group("episode_ko")
        )
    elif anime_marker:
        episode = int(anime_marker.group("episode"))
    elif number_match:
        episode = int(number_match.group("episode"))
    elif trailing_number and not _is_decimal_trailing_number(
        episode_stem, trailing_number,
    ) and not (
        year_match and year_match.start() == trailing_number.start(1)
    ):
        episode = int(trailing_number.group("episode"))
        if compact_episode:
            season, episode, _compact_number = compact_episode

    if season is None:
        filename_season = TV_SEASON_MARKER_RE.search(stem)
        if filename_season:
            season = int(filename_season.group(1))

    for parent in path.parents:
        if SPECIALS_DIR_RE.fullmatch(parent.name.strip()):
            if season is None:
                season = 0
            break
        season_match = re.fullmatch(
            r"(?i)(?:season|s)\s*(\d+)", parent.name.strip(),
        )
        if season_match and not 1900 <= int(season_match.group(1)) <= 2100:
            if season is None:
                season = int(season_match.group(1))
            break

    if episode is not None and season is None:
        season = 1
    if extra_kind or episode == 0:
        season, episode = 0, None

    if episode is None:
        untagged_parts = _untagged_episode_parts(path.stem)
        if untagged_parts and not extra_kind:
            title = untagged_parts[0]

    if not _useful_tv_title(title):
        title = ""
        parents = path.parents
        if root is not None:
            root = Path(root)
            try:
                path.relative_to(root)
            except ValueError:
                parents = ()
        for parent in parents:
            if root is not None and parent == root:
                candidate = _clean_tv_title(parent.name)
                if _useful_tv_title(candidate):
                    title = candidate
                    if year is None:
                        parent_year = next(
                            (
                                match for match in YEAR_RE.finditer(
                                    parent.name,
                                )
                                if match.start() > 0
                            ),
                            None,
                        )
                        if parent_year:
                            year = int(parent_year.group())
                break
            candidate = _clean_tv_title(parent.name)
            if _useful_tv_title(candidate):
                title = candidate
                if year is None:
                    parent_year = next(
                        (
                            match for match in YEAR_RE.finditer(parent.name)
                            if match.start() > 0
                        ),
                        None,
                    )
                    if parent_year:
                        year = int(parent_year.group())
                break

    return title, year, season, episode


def has_explicit_episode_marker(path):
    """Recognize standard and anime episode markers, but not bare years."""
    stem = _fix_ocr_markers(Path(path).stem)
    match = TV_NAME_MARKER_RE.search(stem)
    anime = ANIME_EPISODE_RE.search(stem)
    if anime and _is_year_like_episode(stem, anime):
        anime = None
    return bool(
        (match and any(
            match.group(name)
            for name in ("season", "season_x", "season_word")
        ))
        or CJK_EPISODE_RE.search(stem)
        or anime
    )


def parse_production_code(stem):
    """Return a production code like 3ACV01 from 'Show - 3ACV01 - Title'."""
    stem = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", stem or "")
    stem = FILE_NUMBER_RE.sub("", stem).replace(".", " ").replace("_", " ")
    parts = re.split(r"\s+-\s+", stem.strip(), maxsplit=1)
    if len(parts) != 2:
        return None
    match = PRODUCTION_CODE_RE.match(parts[1].strip())
    return match.group("code").upper() if match else None


def parse_tv_episode_end(path):
    """Return a multi-episode range end, if the filename contains one."""
    match = TV_EPISODE_RE.search(_fix_ocr_markers(Path(path).stem))
    if not match:
        return None
    end = match.group("episode_end") or match.group("episode_end_x")
    return int(end) if end else None


def tv_season_is_explicit(path):
    """Whether a season number came from the filename or its folders."""
    path = Path(path)
    match = TV_NAME_MARKER_RE.search(_fix_ocr_markers(path.stem))
    if match and any(
        match.group(name) for name in ("season", "season_x", "season_word")
    ):
        return True
    if TV_SEASON_MARKER_RE.search(path.stem):
        return True
    for parent in path.parents:
        season = re.fullmatch(
            r"(?i)(?:season|s)\s*(\d+)", parent.name.strip(),
        )
        if season and not 1900 <= int(season.group(1)) <= 2100:
            return True
    return False


def parse_tv_episode(stem):
    """Return (show title, year, season, episode) for standard episode names."""
    parse_stem = _strip_parenthesized_release_tags(stem)
    parse_stem = re.sub(
        r"\[[^\]]*\]|\{[^}]*\}", " ", _fix_ocr_markers(stem),
    )
    parse_stem = _strip_tv_file_number(parse_stem)
    if parse_tv_extra(stem):
        return None
    release_match = TV_RELEASE_TAG_RE.search(parse_stem)
    if release_match and release_match.start() > 0:
        parse_stem = parse_stem[:release_match.start()]
    parse_stem = parse_stem.rstrip(" \t.([{_-")
    match = TV_EPISODE_RE.search(parse_stem)
    if not match:
        compact = parse_tv_compact_episode(stem)
        if compact:
            season, episode, _compact_number = compact
            title = _clean_tv_title(
                re.sub(r"(?<!\d)\d{3,4}$", "", parse_stem).rstrip(" ._-"),
            )
            return title, None, season, episode
        bare_marker = TV_NAME_MARKER_RE.search(parse_stem)
        if bare_marker and bare_marker.group("bare_episode"):
            title, year = parse_name(parse_stem[:bare_marker.start()])
            bracket_title = _bracketed_episode_title(stem)
            return (
                bracket_title or title, year, 1,
                int(bare_marker.group("bare_episode")),
            )
        cjk = CJK_EPISODE_RE.search(parse_stem)
        if cjk:
            title, year = parse_name(parse_stem[:cjk.start()])
            return title, year, 1, int(
                cjk.group("episode_cjk") or cjk.group("episode_jp")
                or cjk.group("episode_ko")
            )
        anime = ANIME_EPISODE_RE.search(parse_stem)
        if (
            anime and not _is_decimal_trailing_number(parse_stem, anime)
            and not _is_year_like_episode(parse_stem, anime)
        ):
            title, year = parse_name(parse_stem[:anime.start()])
            return _clean_tv_title(title), year, 1, int(
                anime.group("episode"),
            )
        number = NUMBERED_EPISODE_RE.fullmatch(parse_stem.strip())
        trailing = TRAILING_EPISODE_RE.search(parse_stem)
        if number:
            return "", None, 1, int(number.group("episode"))
        if trailing and not _is_decimal_trailing_number(
            parse_stem, trailing,
        ):
            title, year = parse_name(parse_stem[:trailing.start(1)])
            if not YEAR_RE.fullmatch(trailing.group("episode")):
                return title, year, 1, int(trailing.group("episode"))
        return None
    season = int(
        match.group("season") or match.group("season_x")
        or match.group("season_word")
    )
    episode = int(
        match.group("episode") or match.group("episode_x")
        or match.group("episode_word")
    )
    title, year = parse_name(parse_stem[:match.start()])
    title = _bracketed_episode_title(stem) or title
    title = re.sub(r"\s+(?:US|UK)$", "", title, flags=re.IGNORECASE)
    if episode < 1:
        return None
    return title, year, season, episode


def parse_tv_episode_in_context(stem, show_title, season_number=1):
    """Infer an episode number from a simple filename inside a known show."""
    if not show_title:
        return None
    if not TV_NAME_MARKER_RE.search(_fix_ocr_markers(stem)):
        match = CONTEXT_NUMBERED_TITLE_RE.fullmatch(stem.strip())
        if match:
            episode = int(match.group("episode"))
            if episode > 0:
                return show_title, None, season_number, episode
    for pattern in (
        CONTEXT_EPISODE_RE, COMPACT_EPISODE_RE, NUMBERED_EPISODE_RE,
    ):
        match = pattern.fullmatch(stem.strip())
        if match:
            episode = int(match.group("episode"))
            if episode > 0:
                return show_title, None, season_number, episode
    return None


def parse_tv_episode_title_in_context(stem):
    """Return the title from a numbered filename inside a known show."""
    match = CONTEXT_NUMBERED_TITLE_RE.fullmatch(stem.strip())
    if not match:
        return None
    return _clean_tv_title(match.group("title")) or None


def parse_tv_episode_title(stem):
    """Return the optional episode title following the season and episode."""
    stem = _fix_ocr_markers(stem)
    match = TV_EPISODE_RE.search(stem)
    if not match:
        parts = _untagged_episode_parts(stem)
        return parts[1] if parts else None
    title, _year = parse_name(stem[match.end():])
    return title or None


def sanitize(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return _avoid_windows_reserved_name(name)


def _id_for_names(choice):
    """(value, tag label) of the match id: 603/tmdb or tt0111161/imdb."""
    composite = choice.get("id")
    if not isinstance(composite, int) or composite <= 0:
        return choice.get("id", ""), "tmdb"
    tag = provider_ids.external_tag(composite)
    if tag is None:
        return provider_ids.native_id(composite), None
    return (tag[1], tag[0])


def folder_name_for(choice):
    """Folder / file name for a movie, using the template from Settings."""
    title, year = choice["title"], choice.get("year") or ""
    id_value, id_label = _id_for_names(choice)
    try:
        name = NAMING["template"].format(
            title=title, year=year, id=id_value
        )
    except (KeyError, ValueError, IndexError):
        name = f"{title} ({year})" if year else title
    name = sanitize(re.sub(r"\(\s*\)|\[\s*\]", "", name))
    tag = (
        f"[{id_label}id-{id_value}]"
        if choice.get("id") and id_label else ""
    )
    if NAMING["tag_id"] and tag and tag not in name:
        name = f"{name} {tag}"
    return name or sanitize(title)


def tv_folder_name_for(choice):
    """Series folders use the official title only, never the movie template."""
    title = choice.get("title")
    return sanitize(title) if isinstance(title, str) else ""


def tv_episode_name_for(choice, max_length=200):
    """Build the required series episode stem; missing metadata is unsafe."""
    season = choice.get("season_number")
    episode = choice.get("episode_number")
    episode_end = choice.get("episode_end_number")
    raw_episode_title = choice.get("episode_title")
    episode_title = (
        sanitize(raw_episode_title)
        if isinstance(raw_episode_title, str) else ""
    )
    template = re.sub(r":00(?=})", ":02d", NAMING["tv_template"])
    needs_episode_title = "{episode_title" in template
    if not (
        isinstance(choice.get("id"), int) and choice["id"] > 0
        and isinstance(season, int) and season >= 0
        and isinstance(episode, int) and episode > 0
        and (episode_title or not needs_episode_title)
    ):
        raise ValueError("TMDB episode information is incomplete.")
    show = tv_folder_name_for(choice)
    if not show:
        raise ValueError("TMDB returned an invalid TV show name.")
    episode_range = None
    if isinstance(episode_end, int) and episode_end > episode:
        episode_match = re.search(r"\{episode(?::([^}]+))?\}", template)
        if episode_match:
            spec = (episode_match.group(1) or "").replace("00", "02")
            start = format(episode, spec)
            end = format(episode_end, spec)
            separator = "-" if template[:episode_match.start()].endswith(
                ("x", "X"),
            ) else "-E"
            episode_range = start + separator + end
            template = (
                template[:episode_match.start()] + "{episode_range}"
                + template[episode_match.end():]
            )
    try:
        format_values = dict(
            title=show,
            year=choice.get("year") or "",
            id=_id_for_names(choice)[0],
            season=season,
            episode=episode,
            episode_title=episode_title,
        )
        if episode_range is not None:
            format_values["episode_range"] = episode_range
        name = template.format(**format_values)
    except (KeyError, ValueError, IndexError):
        end = episode_end if isinstance(episode_end, int) else episode
        name = f"{show} - S{season:02d}E{episode:02d}"
        if end > episode:
            name += f"-E{end:02d}"
        if episode_title:
            name += f" - {episode_title}"
    name = sanitize(name) or show
    if (
        isinstance(episode_end, int) and episode_end > episode
        and len(name) > max_length
    ):
        suffix = ""
        if episode_title and episode_title in name:
            suffix = episode_title
            name = name[:name.rfind(suffix)]
        available = max(0, max_length - len(name))
        name = (name + suffix[:available]).strip(" .-&")
        if len(name) > max_length:
            name = name[:max_length].rstrip(" .-&")
    return name


def norm(value):
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = value.replace("&", "and")
    value = re.sub(r"[^\w ]|_", "", value)
    return re.sub(r"\s+", " ", value).strip()


def fold_diacritics(value):
    """Drop accents from Latin letters only (Amelie == Amélie); keep kana/hangul marks."""
    out = []
    for char in unicodedata.normalize("NFC", value or ""):
        parts = unicodedata.normalize("NFD", char)
        if (
            len(parts) > 1 and ord(parts[0]) < 0x250
            and all(unicodedata.combining(mark) for mark in parts[1:])
        ):
            out.append(parts[0])
        else:
            out.append(char)
    return "".join(out)


def similarity(a, b):
    return SequenceMatcher(
        None, fold_diacritics(norm(a)), fold_diacritics(norm(b)),
    ).ratio()
