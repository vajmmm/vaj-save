"""Filesystem locations for the metadata layer.

Metadata indexes can come from three places, in priority order:

1. an explicit directory persisted in the app config (``libretro_dir``);
2. ``<library_root>/metadata/libretro/`` -- drop-in location next to the archive;
3. the data files bundled with the package (``vajsave/data/libretro/``), which
   ship the compact offline index (``gba.json`` / ``nds.json``) built from the
   real No-Intro DATs by ``tools/build_metadata_index.py``.

None of these must exist; the provider simply finds nothing and every lookup
returns ``None``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Union

# Sub-path inside the local library for user-supplied metadata.
LIBRARY_METADATA_DIR = "metadata"
# Sub-directory holding the libretro/No-Intro ``.dat`` files.
LIBRARY_LIBRETRO_DIR = "libretro"


def bundled_data_dir() -> Path:
    """The package's ``vajsave/data`` directory."""
    return Path(__file__).resolve().parent.parent / "data"


def bundled_libretro_dir() -> Path:
    return bundled_data_dir() / LIBRARY_LIBRETRO_DIR


def default_libretro_dirs(
    library_root: Optional[Union[Path, str]] = None,
    *,
    configured: Optional[Union[str, Iterable[str]]] = None,
) -> List[Path]:
    """Build the ordered list of metadata directories to scan.

    Duplicates are removed while order is preserved, so the first directory that
    defines a digest wins when the same game appears in several indexes.
    """
    dirs: List[Path] = []
    if isinstance(configured, (str, Path)):
        configured_iter: Iterable = [configured]
    else:
        configured_iter = configured or ()
    for value in configured_iter:
        if value:
            dirs.append(Path(value).expanduser())
    if library_root:
        dirs.append(
            Path(library_root).expanduser() / LIBRARY_METADATA_DIR / LIBRARY_LIBRETRO_DIR
        )
    dirs.append(bundled_libretro_dir())
    ordered: List[Path] = []
    seen = set()
    for directory in dirs:
        try:
            key = str(directory)
        except Exception:  # noqa: BLE001
            continue
        if key in seen:
            continue
        seen.add(key)
        ordered.append(directory)
    return ordered


__all__ = [
    "bundled_data_dir",
    "bundled_libretro_dir",
    "default_libretro_dirs",
    "LIBRARY_METADATA_DIR",
    "LIBRARY_LIBRETRO_DIR",
]
