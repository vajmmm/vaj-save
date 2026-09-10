"""Public save-scan API: validate root, dispatch platform scanners, merge results."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set, Union

from .models import SaveEntry, SaveSource, ScanResult
from .platforms import gba, nds, psp, switch, threeds, vita
from .platforms.common import (
    collect_unique_dirs,
    find_pattern_dirs,
    is_safe_path,
    resolved_key,
    safe_iterdir,
)

# Back-compat aliases for tests and external callers that import private helpers.
_MAX_WRAPPER_DEPTH = 2
_is_safe_path = is_safe_path
_safe_iterdir = safe_iterdir
_resolved_key = resolved_key
_find_pattern_dirs = find_pattern_dirs
_collect_unique_dirs = collect_unique_dirs


def scan(root_path: Union[Path, str]) -> ScanResult:
    """Scan a root directory (e.g. mounted USB volume or SD card) and list saves."""
    root = Path(root_path)
    warnings: List[str] = []

    try:
        if not root.exists() or not root.is_dir():
            warnings.append(f"Target path does not exist or is not a directory: {root}")
            return ScanResult(
                root_path=str(root),
                platform="unknown",
                sources=[],
                saves=[],
                warnings=warnings,
            )
        root_resolved = root.resolve()
    except Exception as e:
        warnings.append(f"Failed to access root path {root}: {e}")
        return ScanResult(
            root_path=str(root),
            platform="unknown",
            sources=[],
            saves=[],
            warnings=warnings,
        )

    sources: List[SaveSource] = []
    saves: List[SaveEntry] = []
    seen_source_roots: Set[Path] = set()
    seen_save_paths: Set[Path] = set()

    scanners = (
        psp.scan_psp,
        vita.scan_vita,
        switch.scan_switch,
        threeds.scan_threeds,
        gba.scan_gba,
        nds.scan_nds,
    )
    for scan_fn in scanners:
        scan_fn(
            root,
            root_resolved,
            warnings,
            sources,
            saves,
            seen_source_roots,
            seen_save_paths,
        )

    if not sources:
        platform = "unknown"
    else:
        unique_platforms = list(dict.fromkeys(s.platform for s in sources))
        platform = unique_platforms[0]

    return ScanResult(
        root_path=str(root),
        platform=platform,
        sources=sources,
        saves=saves,
        warnings=warnings,
    )
