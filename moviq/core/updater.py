import logging
import re

import requests


GITHUB_OWNER = "asadse7en"
GITHUB_REPO = "Moviq"
REPO = f"{GITHUB_OWNER}/{GITHUB_REPO}"
RELEASES_URL = f"https://github.com/{REPO}/releases"
LATEST_RELEASE_API_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
)
log = logging.getLogger(__name__)


def _version_tuple(version):
    version = version.removeprefix("v")
    if not re.fullmatch(
        r"\d+(?:\.\d+)*(?:-[0-9A-Za-z.-]+)?"
        r"(?:\+[0-9A-Za-z.-]+)?",
        version,
    ):
        raise ValueError(f"Invalid version: {version}")
    numeric_version = re.split(r"[-+]", version, maxsplit=1)[0]
    return tuple(int(part) for part in numeric_version.split("."))


def check_latest_release(current_version):
    try:
        response = requests.get(
            LATEST_RELEASE_API_URL,
            timeout=8,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"Moviq/{current_version}",
            },
        )
        if getattr(response, "status_code", None) in (403, 429):
            log.warning(
                "GitHub rate limited the update check (HTTP %s).",
                response.status_code,
            )
            return ("rate_limited", None, None)
        response.raise_for_status()
        release = response.json()
        latest = release["tag_name"]
        release_url = release["html_url"]
        if not isinstance(latest, str) or not isinstance(release_url, str):
            raise ValueError("GitHub release response is missing valid fields.")
        if not release_url.startswith(
            f"https://github.com/{REPO}/releases/",
        ):
            raise ValueError("GitHub release response contains an invalid URL.")
        current_version = _version_tuple(current_version)
        latest_version = _version_tuple(latest)
        width = max(len(current_version), len(latest_version))
        current_version += (0,) * (width - len(current_version))
        latest_version += (0,) * (width - len(latest_version))
        result = (
            "available" if latest_version > current_version else "current",
            latest,
            release_url,
        )
    except requests.HTTPError as error:
        if getattr(getattr(error, "response", None), "status_code", None) in (
            403, 429,
        ):
            log.warning("GitHub rate limited the update check: %s", error)
            return ("rate_limited", None, None)
        log.warning("Could not check for updates: %s", error)
        result = ("error", None, None)
    except (
        requests.RequestException, ValueError, KeyError, TypeError,
    ) as error:
        log.warning("Could not check for updates: %s", error)
        result = ("error", None, None)
    return result
