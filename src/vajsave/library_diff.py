"""Lightweight file-level diff between two snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .library_models import Snapshot
from .save_tree import _dir_file_list, _update_from_file


@dataclass
class FileDiff:
    """File difference item between two snapshots."""

    path: str
    status: str  # "added" | "removed" | "modified"
    size_a: Optional[int] = None
    size_b: Optional[int] = None

    @property
    def size_delta(self) -> int:
        return (self.size_b or 0) - (self.size_a or 0)


@dataclass
class VersionDiff:
    """Summary and details of differences between two versions."""

    snapshot_a_id: str
    snapshot_b_id: str
    added_files: List[FileDiff] = field(default_factory=list)
    removed_files: List[FileDiff] = field(default_factory=list)
    modified_files: List[FileDiff] = field(default_factory=list)
    total_files_a: int = 0
    total_files_b: int = 0
    total_size_a: int = 0
    total_size_b: int = 0

    @property
    def file_count_delta(self) -> int:
        return self.total_files_b - self.total_files_a

    @property
    def size_delta(self) -> int:
        return self.total_size_b - self.total_size_a

    @property
    def is_identical(self) -> bool:
        return (
            not self.added_files
            and not self.removed_files
            and not self.modified_files
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    _update_from_file(digest, path)
    return digest.hexdigest()


def _scan_snapshot_files(path: Path) -> Dict[str, Tuple[Path, int]]:
    """Return map of rel_path -> (full_path, size)."""
    if not path.exists():
        return {}
    if path.is_file():
        return {path.name: (path, path.stat().st_size)}

    # Directory walk
    results: Dict[str, Tuple[Path, int]] = {}
    for child, rel, _mtime, size in _dir_file_list(path):
        results[rel] = (child, size)
    return results


def diff_snapshots(
    snapshot_a: Snapshot,
    snapshot_b: Snapshot,
    library_root: Path,
) -> VersionDiff:
    """Compute file-level differences between snapshot_a and snapshot_b."""
    root = Path(library_root)
    path_a = snapshot_a.absolute_path(root)
    path_b = snapshot_b.absolute_path(root)

    files_a = _scan_snapshot_files(path_a)
    files_b = _scan_snapshot_files(path_b)

    total_size_a = sum(size for _p, size in files_a.values())
    total_size_b = sum(size for _p, size in files_b.values())

    diff = VersionDiff(
        snapshot_a_id=snapshot_a.id,
        snapshot_b_id=snapshot_b.id,
        total_files_a=len(files_a),
        total_files_b=len(files_b),
        total_size_a=total_size_a,
        total_size_b=total_size_b,
    )

    all_keys = sorted(set(files_a.keys()) | set(files_b.keys()))

    for key in all_keys:
        in_a = key in files_a
        in_b = key in files_b

        if in_a and not in_b:
            _p, size_a = files_a[key]
            diff.removed_files.append(
                FileDiff(path=key, status="removed", size_a=size_a, size_b=None)
            )
        elif in_b and not in_a:
            _p, size_b = files_b[key]
            diff.added_files.append(
                FileDiff(path=key, status="added", size_a=None, size_b=size_b)
            )
        else:
            p_a, size_a = files_a[key]
            p_b, size_b = files_b[key]
            if size_a != size_b:
                diff.modified_files.append(
                    FileDiff(path=key, status="modified", size_a=size_a, size_b=size_b)
                )
            else:
                # Same size; compare hash
                if _file_sha256(p_a) != _file_sha256(p_b):
                    diff.modified_files.append(
                        FileDiff(path=key, status="modified", size_a=size_a, size_b=size_b)
                    )

    return diff
