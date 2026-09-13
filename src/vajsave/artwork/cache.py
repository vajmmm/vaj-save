"""Identity-hash cover cache with a JSON manifest.

Downloaded covers are named after the *identity hash* of the save they belong
to (``sha1(identity_key)``), so the file name is stable across renames and
cannot collide with a different game.  Each platform gets its own directory::

    <library_root>/covers/<platform>/<identity-hash>.png
    <library_root>/covers/manifest.json

The manifest records one entry per cached file with the exact fields the
contract requires: ``identity_key``, ``platform``, ``provider``,
``canonical_title``, ``remote_url``, ``local_path`` and ``updated_at``.

* a **valid hit** requires a manifest entry *and* the referenced file to exist,
  so a partially written or externally deleted image never looks like a hit;
* an image is only ever committed (file + manifest entry) after Pillow confirms
  it decodes and is not a wide banner, so a 404 HTML page, a truncated download,
  or a landscape screenshot can never pollute the cover cache;
* writes are atomic (temp file renamed into place) to survive a crash mid-write.

The whole module is best-effort: every public method degrades rather than raises.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ..covers import DOWNLOADED_COVER_DIR, identity_hash
from ..persistence import atomic_write_bytes, atomic_write_json

# Directory (under the library root) that holds the covers/ tree.
COVER_CACHE_DIR = DOWNLOADED_COVER_DIR
MANIFEST_NAME = "manifest.json"
_VERSION = 1

# Fields every manifest record must carry (the acceptance contract).
MANIFEST_FIELDS = (
    "identity_key",
    "provider",
    "canonical_title",
    "remote_url",
    "local_path",
    "updated_at",
)

# Same ceiling as the embedded-icon reader: refuse to cache a cover larger than
# this so a hostile/oversized download cannot fill the library.
MAX_COVER_BYTES = 8 * 1024 * 1024

# A few box-art exports are nominally a pixel or two wider than tall after
# trimming transparent borders. Allow that near-square rounding while still
# rejecting PSP's 144×80 banner icons and other clearly landscape artwork.
MAX_COVER_ASPECT_RATIO = 1.10


def is_valid_image_bytes(data: Optional[bytes]) -> bool:
    """True when ``data`` decodes as an image (Pillow) and is non-empty."""
    if not data:
        return False
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:  # noqa: BLE001 - any decode failure means "not an image"
        return False


def is_portrait_image_bytes(data: Optional[bytes]) -> bool:
    """True when ``data`` is decodable and not a wide banner.

    Downloaded artwork is shown in the gallery's portrait card geometry. Square
    and near-square images remain valid; only clearly landscape assets are
    rejected so they cannot be silently center-cropped into an unrelated cover.
    """
    if not data:
        return False
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.verify()
            width, height = image.size
        return width <= height * MAX_COVER_ASPECT_RATIO
    except Exception:  # noqa: BLE001 - any decode failure is not a cover
        return False


def is_portrait_image_file(path: Path, *, max_bytes: int = MAX_COVER_BYTES) -> bool:
    """Validate an existing cover without loading an unbounded image."""
    try:
        size = path.stat().st_size
        if size <= 0 or size > int(max_bytes):
            return False
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
            width, height = image.size
        return width <= height * MAX_COVER_ASPECT_RATIO
    except Exception:  # noqa: BLE001 - stale/corrupt cache entries are misses
        return False


class CoverCache:
    """A directory of downloaded covers plus its manifest."""

    def __init__(
        self,
        root: Union[Path, str, None] = None,
        *,
        max_bytes: int = MAX_COVER_BYTES,
    ) -> None:
        self.root: Optional[Path] = Path(root).expanduser() if root else None
        self.max_bytes = int(max_bytes)
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    # -- naming --------------------------------------------------------------

    @staticmethod
    def identity_hash(identity_key: str) -> str:
        return identity_hash(identity_key)

    @staticmethod
    def _relative_path(platform: str, identity_key: str) -> str:
        plat = (str(platform or "").strip() or "unknown")
        return f"{plat}/{identity_hash(identity_key)}.png"

    @staticmethod
    def path_in(
        root: Union[Path, str, None], platform: str, identity_key: str
    ) -> Optional[Path]:
        """Absolute cover path under ``root`` (the ``covers/`` directory)."""
        if root is None or not identity_key:
            return None
        return Path(root) / CoverCache._relative_path(platform, identity_key)

    def path_for(self, platform: str, identity_key: str) -> Optional[Path]:
        return self.path_in(self.root, platform, identity_key)

    # Back-compat alias for the previous private name.
    _local_path = _relative_path

    # -- lookup --------------------------------------------------------------

    def lookup(self, platform: str, identity_key: str) -> Optional[Path]:
        """Return the cached cover path for a *valid* hit, else ``None``."""
        if self.root is None or not identity_key:
            return None
        with self._lock:
            record = self._entries.get(str(identity_key))
        if not isinstance(record, dict):
            return None
        if str(record.get("platform", "")) != str(platform or "").strip():
            return None
        relative = record.get("local_path")
        if not relative:
            return None
        path = self.root / str(relative)
        try:
            if not path.is_file():
                return None
            size = path.stat().st_size
            if size <= 0 or size > self.max_bytes:
                return None
            if not is_portrait_image_file(path, max_bytes=self.max_bytes):
                return None
        except OSError:
            return None
        return path

    # -- store ---------------------------------------------------------------

    def store(
        self,
        platform: str,
        identity_key: str,
        data: Optional[bytes],
        *,
        provider: str = "libretro",
        canonical_title: str = "",
        remote_url: str = "",
    ) -> Optional[Path]:
        """Validate and commit ``data`` as the cover for ``identity_key``.

        Returns the stored path, or ``None`` when the input is not a usable image
        (in which case nothing on disk or in the manifest is touched).
        """
        if self.root is None or not identity_key:
            return None
        if not data or len(data) > self.max_bytes:
            return None
        if not is_portrait_image_bytes(data):
            return None
        path = self.path_for(platform, identity_key)
        if path is None:
            return None
        relative = self._local_path(platform, identity_key)
        if not atomic_write_bytes(path, data):
            return None
        record = {
            "identity_key": str(identity_key),
            "platform": str(platform or "").strip(),
            "provider": str(provider or ""),
            "canonical_title": str(canonical_title or ""),
            "remote_url": str(remote_url or ""),
            "local_path": relative,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        with self._lock:
            self._entries[str(identity_key)] = record
            payload = {"version": _VERSION, "entries": dict(self._entries)}
            atomic_write_json(self.root / MANIFEST_NAME, payload)
        return path

    # -- remove --------------------------------------------------------------

    def remove(self, platform: str, identity_key: str) -> List[Path]:
        """Delete the cached cover for ``identity_key`` plus its manifest entry.

        Returns the files actually removed, or ``[]`` when the key is unknown or
        was cached under a different platform. Only files that resolve strictly
        inside the ``covers/`` root are ever unlinked.
        """
        if self.root is None or not identity_key:
            return []
        key = str(identity_key)
        with self._lock:
            record = self._entries.get(key)
            if not isinstance(record, dict):
                return []
            if str(record.get("platform", "")) != str(platform or "").strip():
                return []
            candidates: List[Path] = []
            relative = record.get("local_path")
            if relative:
                candidates.append(self.root / str(relative))
            canonical = self.path_for(platform, identity_key)
            if canonical is not None:
                candidates.append(canonical)
            removed: List[Path] = []
            for candidate in candidates:
                if self._delete_within_root(candidate):
                    removed.append(candidate)
            self._entries.pop(key, None)
            payload = {"version": _VERSION, "entries": dict(self._entries)}
            atomic_write_json(self.root / MANIFEST_NAME, payload)
            return removed

    def _delete_within_root(self, path: Path) -> bool:
        """Unlink ``path`` only when it resolves strictly inside the covers root."""
        if self.root is None:
            return False
        try:
            if path.is_symlink():
                return False
            root_resolved = self.root.resolve()
            resolved = path.resolve()
        except OSError:
            return False
        if resolved == root_resolved:
            return False
        try:
            resolved.relative_to(root_resolved)
        except ValueError:
            return False
        try:
            resolved.unlink()
        except OSError:
            return False
        return True

    # -- manifest ------------------------------------------------------------

    def manifest(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {key: dict(value) for key, value in self._entries.items()}

    def load(self) -> None:
        entries: Dict[str, Dict[str, Any]] = {}
        if self.root is not None:
            manifest = self.root / MANIFEST_NAME
            try:
                if manifest.is_file():
                    raw = json.loads(manifest.read_text(encoding="utf-8"))
                    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                        entries = {
                            key: value
                            for key, value in raw["entries"].items()
                            if isinstance(value, dict)
                        }
            except (OSError, ValueError, UnicodeDecodeError):
                entries = {}
        with self._lock:
            self._entries = entries

    def save(self) -> bool:
        with self._lock:
            if self.root is None:
                return False
            payload = {"version": _VERSION, "entries": dict(self._entries)}
            return atomic_write_json(self.root / MANIFEST_NAME, payload)

    def prune(self) -> int:
        """Drop manifest entries whose file vanished; returns entries removed."""
        if self.root is None:
            return 0
        with self._lock:
            removed = 0
            for key, record in list(self._entries.items()):
                relative = record.get("local_path")
                try:
                    exists = bool(relative) and (self.root / str(relative)).is_file()
                except OSError:
                    exists = False
                if not exists:
                    self._entries.pop(key, None)
                    removed += 1
            if removed:
                payload = {"version": _VERSION, "entries": dict(self._entries)}
                atomic_write_json(self.root / MANIFEST_NAME, payload)
        return removed


__all__ = [
    "CoverCache",
    "COVER_CACHE_DIR",
    "MANIFEST_NAME",
    "MANIFEST_FIELDS",
    "is_portrait_image_bytes",
    "is_portrait_image_file",
    "is_valid_image_bytes",
    "MAX_COVER_ASPECT_RATIO",
    "MAX_COVER_BYTES",
]
