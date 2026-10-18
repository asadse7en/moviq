import json
import logging
import queue
import re
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, ttk
import tkinter as tk

from PIL import Image, ImageTk

from ... import resource
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
from ..controls import SegmentedModeToggle
from ..design import LANGUAGES
from ._common import (
    APP_VERSION, ISSUES_URL, LICENSE_URL, TMDB_ATTRIBUTION, TMDB_KEY_URL,
    TMDB_SIGNUP_URL, log,
)
from .about import AboutTabMixin
from .formatting import FormattingTabMixin
from .general import GeneralTabMixin
from .matching import MatchingTabMixin
from .view import SettingsView

check_for_updates = check_latest_release
