"""Shared path helpers for platform scanners."""

from __future__ import annotations

from pathlib import Path
from typing import FrozenSet, Iterable, Iterator, List, Optional, Set, Tuple

# How many extra directory levels above a known layout prefix are allowed
# (e.g. backup/ or outer/inner/ wrapping JKSV or switch/Checkpoint/saves).
MAX_WRAPPER_DEPTH = 2

# How deep a known save directory is searched for categorised/nested saves. Deep
# enough for a ``category/game/`` layout, shallow enough that a pathological tree
# cannot turn one scan into an unbounded walk.
MAX_SAVE_SCAN_DEPTH = 4


def is_safe_path(path: Path, root_resolved: Path) -> bool:
    """Ensure path does not escape the resolved root via symlinks."""
    try:
        resolved = path.resolve()
        return resolved.is_relative_to(root_resolved)
    except Exception:
        return False


def safe_iterdir(path: Path, warnings: List[str]) -> List[Path]:
    """Safely list directory contents, catching permission/OS errors."""
    try:
        if not path.is_dir():
            return []
        return sorted(list(path.iterdir()))
    except (PermissionError, FileNotFoundError, OSError) as e:
        warnings.append(f"Cannot access directory {path}: {e}")
        return []


def iter_save_files(
    directory: Path,
    root_resolved: Path,
    warnings: List[str],
    extensions: FrozenSet[str],
    *,
    max_depth: int = MAX_SAVE_SCAN_DEPTH,
) -> Iterator[Path]:
    """Yield regular save files under ``directory``, with bounded recursion.

    Save folders are frequently categorised (``SAVEGAME/Action/Game.sav``), so a
    known save directory is searched a few levels deep.  The walk is deliberately
    conservative:

    * symlinks are never followed -- neither directory nor file links are yielded;
    * a candidate that resolves outside ``root_resolved`` is ignored;
    * recursion stops after ``max_depth`` levels below ``directory``.
    """

    def walk(current: Path, depth: int) -> Iterator[Path]:
        for item in safe_iterdir(current, warnings):
            try:
                if item.is_symlink():
                    continue
                if item.is_dir():
                    if depth < max_depth and is_safe_path(item, root_resolved):
                        yield from walk(item, depth + 1)
                elif item.is_file() and item.suffix.lower() in extensions:
                    if is_safe_path(item, root_resolved):
                        yield item
            except OSError:
                continue

    yield from walk(directory, 0)


def resolved_key(path: Path) -> Optional[Path]:
    try:
        return path.resolve()
    except Exception:
        return None


def find_pattern_dirs(
    root: Path,
    parts: Tuple[str, ...],
    root_resolved: Path,
    warnings: List[str],
    max_wrapper_depth: int = MAX_WRAPPER_DEPTH,
) -> List[Path]:
    """Find dirs matching root/{wrappers 0..N}/parts without escaping root."""
    found: List[Path] = []
    seen: Set[Path] = set()

    def consider(candidate: Path) -> None:
        if not candidate.is_dir() or not is_safe_path(candidate, root_resolved):
            return
        key = resolved_key(candidate)
        if key is None or key in seen:
            return
        seen.add(key)
        found.append(candidate)

    consider(root.joinpath(*parts))

    if max_wrapper_depth < 1:
        return found

    for child in safe_iterdir(root, warnings):
        if not child.is_dir() or not is_safe_path(child, root_resolved):
            continue
        consider(child.joinpath(*parts))
        if max_wrapper_depth < 2:
            continue
        for grandchild in safe_iterdir(child, warnings):
            if not grandchild.is_dir() or not is_safe_path(grandchild, root_resolved):
                continue
            consider(grandchild.joinpath(*parts))

    return found


def collect_unique_dirs(dirs: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen: Set[Path] = set()
    for d in dirs:
        key = resolved_key(d)
        if key is None or key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def find_param_sfo(directory: Path, root_resolved: Path, warnings: List[str]) -> Optional[Path]:
    for child in safe_iterdir(directory, warnings):
        if child.name.upper() == "PARAM.SFO" and child.is_file() and is_safe_path(child, root_resolved):
            return child
    return None
