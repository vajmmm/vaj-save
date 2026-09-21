"""Game Boy Color roms/gbc sibling-save layouts."""

from __future__ import annotations

from pathlib import Path
from typing import List, Set

from ..models import SaveEntry, SaveSource
from .cartridge_files import scan_cartridge_platform

_FINGERPRINT_DIRS = ("EDGB", "GBOS")


def scan_gbc(
    root: Path,
    root_resolved: Path,
    warnings: List[str],
    sources: List[SaveSource],
    saves: List[SaveEntry],
    seen_source_roots: Set[Path],
    seen_save_paths: Set[Path],
    *,
    standard_root: bool = False,
) -> None:
    scan_cartridge_platform(
        "gbc",
        root,
        root_resolved,
        warnings,
        sources,
        saves,
        seen_source_roots,
        seen_save_paths,
        standard_root=standard_root,
        fingerprint_dirs=_FINGERPRINT_DIRS,
    )
