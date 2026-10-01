"""Read-only MTP/WPD access used to pull handheld saves over USB.

This module defines the transport-agnostic contract shared by the Windows WPD
backend (``mtp_windows``), the incremental puller (``mtp_fetch``) and the
AppState integration (``mtp_session``).  It intentionally exposes only storage
listing, directory listing and download operations, so the app can never write
to a handheld.  Tests drive an in-memory fake client implementing the same
contract, so no real WPD/COM is touched.

Only ``Saves`` storages (and the save-related SD-card subtrees) are ever read;
an ``Installed games`` NSP storage, the Album and the whole SD card are never
mirrored.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Protocol, Tuple

from .remote_ftp import sanitize_component

MTP_CACHE_DIRNAME = "mtp-cache"

# Characters Windows cannot use in a file or directory name.
_WINDOWS_INVALID_CHARS = '<>:"|?*'

_SAVES_STORAGE_RE = re.compile(r"^(?:\d+:\s*)?saves$", re.IGNORECASE)
_SWITCH_NAME_RE = re.compile(r"switch|nintendo|dbi|checkpoint", re.IGNORECASE)
_SD_STORAGE_RE = re.compile(r"(?:^|[^a-z])sd(?:[^a-z]|$)", re.IGNORECASE)

# SD-card subtrees that hold exported saves.  The whole SD card (NSP installs,
# Album, System) is never mirrored.
SD_SAVE_SUBTREES: Tuple[str, ...] = (
    "switch/Checkpoint/saves",
    "JKSV",
    "switch/DBI/saves",
)


class MtpError(Exception):
    """A remote MTP/WPD operation failed (open, listing, download)."""


class MtpNotFound(MtpError):
    """The requested storage or object does not exist.

    A missing *optional* SD subtree is expected and skipped; a missing file in
    the Saves tree is a genuine failure.
    """


@dataclass(frozen=True)
class MtpStorage:
    """One MTP storage exposed by a portable device (e.g. ``7: Saves``)."""

    storage_id: str
    name: str
    description: str = ""

    @property
    def is_saves(self) -> bool:
        return _SAVES_STORAGE_RE.match((self.name or "").strip()) is not None

    @property
    def is_sd_card(self) -> bool:
        clean = (self.name or "").strip()
        if re.search(r"install", clean, re.IGNORECASE):
            return False
        return _SD_STORAGE_RE.search(clean) is not None


@dataclass(frozen=True)
class MtpDevice:
    """A discovered WPD/MTP portable device and its storages."""

    device_id: str
    friendly_name: str
    storages: Tuple[MtpStorage, ...] = ()


@dataclass(frozen=True)
class MtpEntry:
    """One directory entry returned by a storage listing."""

    name: str
    is_dir: bool
    size: int = 0
    modified: str = ""


class MtpClient(Protocol):
    """The read-only surface ``mtp_fetch`` drives (injectable in tests)."""

    def connect(self) -> "MtpClient":
        ...

    def list_storages(self) -> List[MtpStorage]:
        ...

    def list_dir(self, storage_id: str, remote_path: str) -> List[MtpEntry]:
        ...

    def download(self, storage_id: str, remote_path: str, local_path: Path) -> int:
        ...

    def close(self) -> None:
        ...


def sanitize_mtp_component(name: object) -> Optional[str]:
    """Return a Windows-safe single path component, or ``None`` when unsafe.

    Reuses the FTP sanitiser (rejects separators, NUL, empty, ``.``/``..``) and
    additionally maps the characters Windows forbids in file names (``:``,
    ``<``, ``>``, ``"``, ``|``, ``?``, ``*`` and control characters) to ``_``.
    """
    base = sanitize_component(name)
    if base is None:
        return None
    cleaned = "".join(
        "_" if (ch in _WINDOWS_INVALID_CHARS or ord(ch) < 32) else ch for ch in base
    ).rstrip(" .")
    if not cleaned or cleaned in (".", ".."):
        return None
    return cleaned


def is_switch_mtp_device(device: MtpDevice) -> bool:
    """True when ``device`` looks like a DBI/Checkpoint Switch over MTP.

    Matches a DBI/Switch/Checkpoint/Nintendo friendly name, or any storage whose
    name is a ``Saves`` storage (e.g. ``7: Saves``).  Phones, cameras and other
    generic MTP devices are rejected.
    """
    if _SWITCH_NAME_RE.search(device.friendly_name or ""):
        return True
    return any(storage.is_saves for storage in device.storages)


def cache_dir_name(device_id: object) -> str:
    """A filesystem-safe directory name for ``device_id`` (hashed if unsafe)."""
    raw = "" if device_id is None else str(device_id)
    clean = sanitize_mtp_component(raw)
    if clean is not None:
        return clean[:80]
    return "dev-" + hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


__all__ = [
    "MTP_CACHE_DIRNAME",
    "SD_SAVE_SUBTREES",
    "MtpClient",
    "MtpDevice",
    "MtpEntry",
    "MtpError",
    "MtpNotFound",
    "MtpStorage",
    "cache_dir_name",
    "is_switch_mtp_device",
    "sanitize_mtp_component",
]
