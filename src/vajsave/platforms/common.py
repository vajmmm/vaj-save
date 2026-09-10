"""Shared path helpers for platform scanners."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

# How many extra directory levels above a known layout prefix are allowed
# (e.g. backup/ or outer/inner/ wrapping JKSV or switch/Checkpoint/saves).
MAX_WRAPPER_DEPTH = 2


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
