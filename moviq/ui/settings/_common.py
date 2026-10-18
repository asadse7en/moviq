from ... import __version__ as APP_VERSION
from ...core.formatting import (
    CUSTOM_ID, MOVIE_DEFAULT_ID, MOVIE_FORMATS, TV_DEFAULT_ID, TV_FORMATS,
    format_pattern,
)
from ...core.settings_store import (
    MOVIE_SAMPLE, TV_SAMPLE, check_tmdb_key, render_pattern,
)
from ...core.updater import RELEASES_URL, REPO, check_latest_release


ISSUES_URL = f"https://github.com/{REPO}/issues"
LICENSE_URL = f"https://github.com/{REPO}/community/license"
TMDB_KEY_URL = "https://www.themoviedb.org/settings/api"
TMDB_SIGNUP_URL = "https://www.themoviedb.org/signup"
TMDB_ATTRIBUTION = (
    "This product uses the TMDB API but is not endorsed or certified by TMDB."
)
