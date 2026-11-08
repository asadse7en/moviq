#!/usr/bin/env python3
"""Create a release version, commit it, and tag it without pushing."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_PATH = REPO_ROOT / "moviq" / "__init__.py"

SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-rc\.(0|[1-9]\d*))?$"
)
BASE_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
VERSION_RE = re.compile(r"(?m)^__version__\s*=\s*[\"']([^\"']+)[\"']")


def read_version() -> str:
    text = INIT_PATH.read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if match is None:
        raise SystemExit(f"Could not find __version__ in {INIT_PATH}")
    current = match.group(1)
    if not SEMVER_RE.fullmatch(current):
        raise SystemExit(f"Current version {current!r} is not valid semver.")
    return current


def bump_base_version(version: str, step: str) -> str:
    match = SEMVER_RE.fullmatch(version)
    if match is None:
        raise SystemExit(f"Version {version!r} is not valid semver.")
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    if step == "major":
        return f"{major + 1}.0.0"
    if step == "minor":
        return f"{major}.{minor + 1}.0"
    if step == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SystemExit(f"Unsupported bump step: {step!r}")


def next_version_for(target: str, current: str, rc_mode: bool) -> str:
    if target in {"patch", "minor", "major"}:
        base = bump_base_version(current, target)
    else:
        if not BASE_RE.fullmatch(target):
            raise SystemExit(
                f"Version must be X.Y.Z without prerelease suffix; got {target!r}."
            )
        base = target

    if not rc_mode:
        return base

    rc_base = base
    current_match = SEMVER_RE.fullmatch(current)
    if current_match and current_match.group(4) is not None:
        current_base = ".".join(current_match.group(1, 2, 3))
        if current_base == rc_base:
            rc_number = int(current_match.group(4)) + 1
            return f"{rc_base}-rc.{rc_number}"

    return f"{rc_base}-rc.1"


def ensure_clean_worktree() -> None:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if proc.stdout.strip():
        raise SystemExit(
            "Git working tree is dirty; commit or stash changes before creating a release."
        )


def ensure_tag_missing(version: str) -> None:
    proc = subprocess.run(
        ["git", "tag", "--list", f"v{version}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    if proc.stdout.strip():
        raise SystemExit(f"Git tag v{version} already exists.")


def write_version(version: str) -> None:
    text = INIT_PATH.read_text(encoding="utf-8")
    updated = VERSION_RE.sub(f'__version__ = "{version}"', text, count=1)
    if updated == text:
        raise SystemExit(f"Could not update __version__ in {INIT_PATH}")
    INIT_PATH.write_text(updated, encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a release tag from moviq.__version__ without pushing."
    )
    parser.add_argument(
        "target",
        help="patch | minor | major | X.Y.Z",
    )
    parser.add_argument(
        "--rc",
        action="store_true",
        help="Add or increment a -rc.N suffix.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current = read_version()
    next_version = next_version_for(args.target, current, args.rc)

    if not SEMVER_RE.fullmatch(next_version):
        raise SystemExit(f"Computed version {next_version!r} is not valid semver.")

    ensure_clean_worktree()
    ensure_tag_missing(next_version)
    write_version(next_version)

    subprocess.run(
        ["git", "commit", "-am", f"release: v{next_version}"],
        cwd=REPO_ROOT,
        check=True,
    )
    subprocess.run(["git", "tag", f"v{next_version}"], cwd=REPO_ROOT, check=True)

    print(f"Version: {next_version}")
    print("Push commands:")
    print("  git push origin HEAD")
    print(f"  git push origin v{next_version}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Command failed with exit code {exc.returncode}: {exc}")
