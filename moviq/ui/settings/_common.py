import logging

from ... import __version__ as APP_VERSION
from ...core.formatting import (
    CUSTOM_ID, MOVIE_DEFAULT_ID, MOVIE_FORMATS, TV_DEFAULT_ID, TV_FORMATS,
    format_component, format_pattern,
)
from ...core.settings_store import (
    DEFAULTS, MOVIE_PRESETS, MOVIE_SAMPLE, TV_PRESETS, TV_SAMPLE,
    SettingsStore, _migrate_formatting_settings, check_tmdb_key,
    render_pattern, resolve_system_theme, validate_pattern,
)
from ...core.tmdb import MAX_WORKERS
from ...core.updater import (
    GITHUB_OWNER, GITHUB_REPO, LATEST_RELEASE_API_URL, RELEASES_URL, REPO,
    _version_tuple, check_latest_release,
)
from ..design import LANGUAGES


ISSUES_URL = f"https://github.com/{REPO}/issues"
LICENSE_URL = f"https://github.com/{REPO}/community/license"
TMDB_KEY_URL = "https://www.themoviedb.org/settings/api"
TMDB_SIGNUP_URL = "https://www.themoviedb.org/signup"
TMDB_ATTRIBUTION = (
    "This product uses the TMDB API but is not endorsed or certified by TMDB."
)
log = logging.getLogger("moviq.ui.settings")
