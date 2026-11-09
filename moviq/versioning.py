"""Source-checkout version reading and updates."""

import os
import re
import stat
from pathlib import Path


_SEMVER_RE = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
)
_VERSION_LINE_RE = re.compile(
    r"(?m)^__version__\s*=\s*(['\"])(?P<version>[^'\"\r\n]+)\1[^\r\n]*(?:\r?\n|$)",
)
INIT_PATH = Path(__file__).with_name("__init__.py")


def _read_version_line(path):
    with Path(path).open("r", encoding="utf-8", newline="") as source:
        text = source.read()
    match = _VERSION_LINE_RE.search(text)
    if match is None:
        raise ValueError(f"Could not find __version__ in {path}")
    version = match.group("version")
    if _SEMVER_RE.fullmatch(version) is None:
        raise ValueError(f"Invalid current version: {version}")
    return text, match, version


def read_version(path=None):
    if path is None:
        path = INIT_PATH
    return _read_version_line(path)[2]


def bump_version(current, part):
    match = _SEMVER_RE.fullmatch(current)
    if match is None:
        raise ValueError(f"Invalid current version: {current}")
    major, minor, patch = (int(value) for value in match.groups())

    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"

    exact_match = _SEMVER_RE.fullmatch(part)
    if exact_match is None:
        raise ValueError(f"Invalid version or bump part: {part}")
    current_parts = tuple(int(value) for value in match.groups())
    target_parts = tuple(int(value) for value in exact_match.groups())
    if target_parts <= current_parts:
        raise ValueError(
            f"Version must be greater than current version {current}",
        )
    return part


def is_writable(path=None):
    if path is None:
        path = INIT_PATH
    path = Path(path)
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(
        mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
    ) and os.access(path, os.W_OK)


def write_version(version, path=None):
    if path is None:
        path = INIT_PATH
    if _SEMVER_RE.fullmatch(version) is None:
        raise ValueError(f"Invalid version: {version}")
    text, match, _current = _read_version_line(path)
    start, end = match.span("version")
    updated = text[:start] + version + text[end:]
    with Path(path).open("w", encoding="utf-8", newline="") as destination:
        destination.write(updated)
