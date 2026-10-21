import os
import re
import shutil
import stat
from pathlib import Path

from .config import IMAGE_EXT, SIDECAR_EXT, SKIP_DIRS, VIDEO_EXT
from .naming import (
    folder_name_for, norm, parse_tv_episode_in_context, parse_tv_name,
    similarity, tv_episode_name_for, tv_folder_name_for,
)

_GENERIC_TV_DIRS = {
    "download", "downloads", "episode", "episodes", "folder", "library",
    "media", "movies", "new folder", "season", "series", "shows", "tv",
    "tv shows", "video", "videos",
}
_SUBTITLE_EXT = SIDECAR_EXT - {".nfo"}


class Plan:
    def __init__(self, dest, dest_dir, rename_dir, new_stem):
        self.dest = dest
        self.dest_dir = dest_dir
        self.rename_dir = rename_dir
        self.new_stem = new_stem
        self.merged = False
        self.cleanup_root = None
        self.source_show_dir = None
        self.source_episode_dir = None
        self.move_generic_assets = False
        self.preserve_source_dirs = False

def is_hidden_path(path):
    path = Path(path)
    if path.name.startswith("."):
        return True
    try:
        attributes = getattr(path.stat(), "st_file_attributes", 0)
    except OSError:
        return False
    hidden_attribute = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    return bool(attributes & hidden_attribute)

_SAMPLE_WORD_RE = re.compile(r"(?<![a-z0-9])(?:sample|trailer)(?![a-z0-9])")
SAMPLE_MAX_BYTES = 300 * 1024 * 1024


def _looks_like_sample(p):
    """Skip only small files whose name has a sample/trailer word."""
    words = [w for w in re.split(r"[\s._\-\[\]()]+", p.stem.lower()) if w]
    if not words or words[-1] not in ("sample", "trailer"):
        return False
    try:
        size = p.stat().st_size
    except OSError:
        return True
    return 0 < size <= SAMPLE_MAX_BYTES


def is_movie_file(p):
    return (not is_hidden_path(p) and p.suffix.lower() in VIDEO_EXT
            and not _looks_like_sample(p))

def count_videos(folder):
    try:
        return sum(1 for p in folder.iterdir() if p.is_file() and is_movie_file(p))
    except OSError:
        return 0

def _same_path_text(a, b):
    return os.fspath(a) == os.fspath(b)

def scan_dir(folder):
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [
            dirname for dirname in dirnames
            if dirname.lower() not in SKIP_DIRS
            and not is_hidden_path(Path(dirpath) / dirname)
        ]
        for fn in filenames:
            p = Path(dirpath) / fn
            if is_movie_file(p):
                found.append(p)
    return sorted(found)

def tv_root_for_folder(folder, files):
    """Use the selected show's parent as root for recognized episode batches."""
    folder = Path(folder)
    files = [Path(path) for path in files]
    episodes = []
    for path in files:
        parsed = parse_tv_name(path)
        if parsed[0] and parsed[2] is not None and parsed[3] is not None:
            episodes.append(parsed)
    if episodes and len({norm(episode[0]) for episode in episodes}) == 1:
        title_tokens = norm(episodes[0][0]).split()
        folder_tokens = re.sub(
            r"[^a-z0-9]+", " ", folder.name.lower()
        ).split()
        if (
            title_tokens
            and (
                folder_tokens[:len(title_tokens)] == title_tokens
                or similarity(folder.name, episodes[0][0]) >= 0.8
            )
        ):
            if norm(folder.parent.name) == norm(folder.name):
                return folder.parent.parent
            return folder.parent
    elif not episodes:
        contextual = [
            parse_tv_episode_in_context(path.stem, folder.name)
            for path in files
        ]
        child_show_dirs = set()
        for path in files:
            try:
                relative = path.relative_to(folder)
            except ValueError:
                continue
            if len(relative.parts) > 1:
                first_dir = relative.parts[0]
                normalized_dir = norm(first_dir)
                if (
                    normalized_dir not in _GENERIC_TV_DIRS
                    and not re.fullmatch(
                        r"(?i)(?:season|series|s)[ ._-]*\d{1,2}",
                        first_dir,
                    )
                ):
                    child_show_dirs.add(normalized_dir)
        if (
            folder.name
            and norm(folder.name) not in _GENERIC_TV_DIRS
            and contextual
            and all(contextual)
            and not child_show_dirs
        ):
            return folder.parent
    return folder

def make_plan(path, root, choice, make_folder, rename_folder, rename_file,
              nvideos, folder_name=None, file_stem=None):
    """Decide where a movie should end up."""
    fname = folder_name or folder_name_for(choice)
    parent = path.parent
    # "Already has its own folder": the folder holds only this movie and is
    # inside the scanned root (or is clearly named after the movie).
    own = parent.name == fname or (
        nvideos == 1 and (parent != root or similarity(parent.name, fname) >= 0.7))
    new_stem = (
        file_stem if file_stem is not None
        else fname if rename_file else path.stem
    )
    rename_dir = None
    if own:
        dest_dir = parent
        if rename_folder and parent.name != fname:
            rename_dir = parent.with_name(fname)
            dest_dir = rename_dir
    else:
        dest_dir = parent / fname if make_folder else parent
    plan = Plan(dest_dir / (new_stem + path.suffix), dest_dir, rename_dir,
                new_stem)
    plan.move_generic_assets = nvideos == 1
    return plan


def _tv_source_show_dir(path, root):
    path, root = Path(path), Path(root)
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.parent
    for part in relative.parts[:-1]:
        if (
            norm(part)
            and norm(part) not in _GENERIC_TV_DIRS
            and not re.fullmatch(r"(?i)(?:season|series|s)[ ._-]*\d{1,2}", part)
        ):
            return root / part
    return root

def make_tv_plan(
    path, root, choice, organize_folders=True, *, show_folder=None,
    season_folder=None, file_stem=None, create_show_folder=None,
    create_season_folder=None,
):
    """Plan TV output from the imported library root and TMDB episode data."""
    suffix = Path(path).suffix
    new_stem = file_stem or tv_episode_name_for(
        choice, max_length=max(1, 200 - len(suffix)),
    )
    if create_show_folder is None:
        create_show_folder = organize_folders
    if create_season_folder is None:
        create_season_folder = organize_folders
    show_folder = show_folder or tv_folder_name_for(choice)
    season_folder = season_folder or f"Season {choice['season_number']:02d}"
    root = Path(root)
    show_dir = (
        root / show_folder
        if create_show_folder and root.name != show_folder else root
    )
    dest_dir = show_dir / season_folder if create_season_folder else show_dir
    plan = Plan(dest_dir / (new_stem + Path(path).suffix), dest_dir, None, new_stem)
    plan.cleanup_root = (
        root if create_show_folder or create_season_folder else None
    )
    plan.tv_show_dir = show_dir
    plan.source_show_dir = _tv_source_show_dir(path, root)
    plan.source_episode_dir = Path(path).parent
    return plan

def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False

def _conflict(dst, src):
    return dst.exists() and not _same_file(dst, src)

def _is_asset(path):
    return path.suffix.lower() in SIDECAR_EXT | IMAGE_EXT

def _is_season_poster(path):
    return (
        path.suffix.lower() in IMAGE_EXT
        and re.match(r"^season[\s._-]*\d+(?:$|[\s._-])", path.stem, re.IGNORECASE)
        is not None
    )

def _move_asset(src, dest_dir, ops):
    dst = dest_dir / src.name
    if _same_path_text(dst, src) or _same_file(dst, src):
        return
    if dst.exists():
        stem, suffix = dst.stem, dst.suffix
        index = 2
        while dst.exists():
            dst = dest_dir / f"{stem} ({index}){suffix}"
            index += 1
    _move_without_overwrite(src, dst)
    ops.append(("move", src, dst))

def move_files(
    video, dest_dir, new_stem, ops, include_images=True,
    rename_subtitles=True,
):
    """Move video and attached subtitle, metadata, and artwork files."""
    old_stem = video.stem
    group = [video]
    attached_exts = SIDECAR_EXT | IMAGE_EXT if include_images else SIDECAR_EXT
    for p in video.parent.iterdir():
        if (p.is_file() and p != video and p.suffix.lower() in attached_exts
                and p.name.lower().startswith(old_stem.lower())
                and p.name[len(old_stem):].startswith((".", "-", "_", " "))):
            group.append(p)
    for p in group:
        new_name = (
            new_stem + p.name[len(old_stem):]
            if rename_subtitles or p.suffix.lower() not in _SUBTITLE_EXT
            else p.name
        )
        dst = dest_dir / new_name
        if _same_path_text(dst, p):
            continue
        if _conflict(dst, p):
            if p is video:
                raise FileExistsError(f"A file with the target name already exists "
                                      f"(maybe a duplicate copy): {dst}")
            continue
        if _same_file(dst, p):               # case-only rename
            os.rename(p, dst)
        else:
            _move_without_overwrite(p, dst)
        ops.append(("move", p, dst))

def _move_tv_assets(plan, ops):
    source_show_dir = plan.source_show_dir
    show_dir = getattr(plan, "tv_show_dir", plan.dest_dir.parent)
    source_episode_dir = plan.source_episode_dir
    if source_show_dir.is_dir():
        for path in source_show_dir.iterdir():
            if path.is_file() and path.suffix.lower() not in VIDEO_EXT:
                _move_asset(path, show_dir, ops)
    if (not source_episode_dir.is_dir()
            or source_episode_dir == source_show_dir):
        return
    for dirpath, _, filenames in os.walk(source_episode_dir):
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in VIDEO_EXT:
                continue
            if _is_season_poster(path):
                _move_asset(path, show_dir, ops)
                continue
            relative_dir = path.parent.relative_to(source_episode_dir)
            asset_dest_dir = plan.dest_dir / relative_dir
            _ensure_dir(asset_dest_dir, ops)
            _move_asset(path, asset_dest_dir, ops)

def _ensure_dir(directory, ops):
    missing = []
    current = directory
    while not current.exists():
        missing.append(current)
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        ops.append(("mkdir", path))

def _move_without_overwrite(src, dst):
    """Move a file without replacing a destination that appeared meanwhile."""
    if os.path.splitdrive(os.path.abspath(src))[0].casefold() != (
        os.path.splitdrive(os.path.abspath(dst))[0].casefold()
    ):
        if dst.exists():
            raise FileExistsError(dst)
        shutil.move(str(src), str(dst))
        return
    if dst.exists():
        raise FileExistsError(dst)
    os.replace(src, dst)

def _remove_empty_source_dirs(source_episode_dir, ops):
    if not source_episode_dir.is_dir():
        return
    for dirpath, dirnames, _ in os.walk(source_episode_dir, topdown=False):
        for dirname in dirnames:
            directory = Path(dirpath) / dirname
            try:
                directory.rmdir()
            except OSError:
                continue
            ops.append(("rmdir", directory))

def perform_plan(path, plan, ops, rename_subtitles=True):
    src, old_dir = path, path.parent
    if plan.rename_dir:
        if _conflict(plan.rename_dir, path.parent):
            plan.merged = True          # target folder exists: move into it
        else:
            os.rename(path.parent, plan.rename_dir)
            ops.append(("rename_dir", path.parent, plan.rename_dir))
            src = plan.rename_dir / path.name
    _ensure_dir(plan.dest_dir, ops)
    move_files(
        src, plan.dest_dir, plan.new_stem, ops,
        include_images=plan.source_show_dir is None,
        rename_subtitles=rename_subtitles,
    )
    if plan.source_show_dir is not None:
        _move_tv_assets(plan, ops)
        if not plan.preserve_source_dirs:
            _remove_empty_source_dirs(plan.source_episode_dir, ops)
    elif plan.move_generic_assets:
        for asset in src.parent.iterdir():
            if asset.is_file() and _is_asset(asset):
                _move_asset(asset, plan.dest_dir, ops)
    cleanup_root = None if plan.preserve_source_dirs else plan.cleanup_root
    if cleanup_root:
        folder = src.parent
        while folder != cleanup_root and cleanup_root in folder.parents:
            if folder.exists():
                try:
                    folder.rmdir()
                except OSError:
                    break
                ops.append(("rmdir", folder))
            folder = folder.parent
    if (
        not plan.preserve_source_dirs
        and plan.merged and old_dir.exists() and not any(old_dir.iterdir())
    ):
        old_dir.rmdir()                 # old folder is empty now
        ops.append(("rmdir", old_dir))
    return plan.dest

def undo_ops(ops):
    errors = []
    for op in reversed(ops):
        try:
            if op[0] == "move":
                shutil.move(str(op[2]), str(op[1]))
            elif op[0] == "rename_dir":
                os.rename(op[2], op[1])
            elif op[0] == "rmdir":
                op[1].mkdir(parents=True, exist_ok=True)
            elif op[0] == "mkdir":
                try:
                    op[1].rmdir()
                except OSError:
                    pass
        except Exception as e:
            errors.append(str(e))
    return errors

def rebase(p, old, new):
    try:
        return new / p.relative_to(old)
    except ValueError:
        return p
