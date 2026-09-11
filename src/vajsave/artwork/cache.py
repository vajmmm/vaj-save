"""Content-addressed cover cache with a JSON manifest.

Downloaded covers are named after the *identity hash* of the save they belong to
(``sha1(identity_key)``), so the file name is stable across renames and cannot
collide with a different game.  A JSON manifest records every cached file so the
cache can be validated and pruned:

* a **valid hit** requires both a manifest entry *and* an on-disk file, so a
  partially written or externally deleted image never looks like a hit;
* an image is only ever committed (file + manifest entry) after Pillow confirms
  it decodes, so a 404 HTML page or a truncated download can never pollute the
  cache;
* writes are atomic (temp file renamed into place) to survive a crash mid-write.

The whole module is best-effort: every public method degrades rather than raises.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional, Union

COVER_CACHE_DIR = "covers_cache"
MANIFEST_NAME = "manifest.json"
_VERSION = 1

# Same ceiling as the embedded-icon reader: refuse to cache a cover larger than
# this so a hostile/oversized download cannot fill the library.
MAX_COVER_BYTES = 8 * 1024 * 1024


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
        self._entries: Dict[str, Dict[str, Any]] = {}
        self.load()

    # -- naming --------------------------------------------------------------

    @staticmethod
    def identity_hash(identity_key: str) -> str:
        return hashlib.sha1(str(identity_key or "").encode("utf-8")).hexdigest()

    def path_for(self, identity_key: str) -> Optional[Path]:
        if self.root is None:
            return None
        return self.root / f"{self.identity_hash(identity_key)}.png"

    # -- lookup --------------------------------------------------------------

    def lookup(self, identity_key: str) -> Optional[Path]:
        """Return the cached cover path for a *valid* hit, else ``None``."""
        if self.root is None or not identity_key:
            return None
        digest = self.identity_hash(identity_key)
        record = self._entries.get(digest)
        if not isinstance(record, dict):
            return None
        filename = record.get("file") or f"{digest}.png"
        path = self.root / filename
        try:
            if not path.is_file():
                return None
            if path.stat().st_size <= 0 or path.stat().st_size > self.max_bytes:
                return None
        except OSError:
            return None
        return path

    # -- store ---------------------------------------------------------------

    def store(
        self,
        identity_key: str,
        data: Optional[bytes],
        *,
        url: Optional[str] = None,
        source: str = "libretro",
    ) -> Optional[Path]:
        """Validate and commit ``data`` as the cover for ``identity_key``.

        Returns the stored path, or ``None`` when the input is not a usable image
        (in which case nothing on disk or in the manifest is touched).
        """
        if self.root is None or not identity_key:
            return None
        if not data or len(data) > self.max_bytes:
            return None
        if not is_valid_image_bytes(data):
            return None
        path = self.path_for(identity_key)
        if path is None:
            return None
        digest = self.identity_hash(identity_key)
        tmp = path.with_name(path.name + ".tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        self._entries[digest] = {
            "identity_key": str(identity_key),
            "file": path.name,
            "url": url or "",
            "source": source,
            "sha1": hashlib.sha1(data).hexdigest(),
            "bytes": len(data),
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.save()
        return path

    # -- manifest ------------------------------------------------------------

    def manifest(self) -> Dict[str, Dict[str, Any]]:
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
        self._entries = entries

    def save(self) -> bool:
        if self.root is None:
            return False
        manifest = self.root / MANIFEST_NAME
        payload = {"version": _VERSION, "entries": dict(self._entries)}
        tmp = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = manifest.with_name(manifest.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(manifest)
        except (OSError, TypeError, ValueError):
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        return True

    def prune(self) -> int:
        """Drop manifest entries whose file vanished; returns entries removed."""
        if self.root is None:
            return 0
        removed = 0
        for digest, record in list(self._entries.items()):
            filename = record.get("file") or f"{digest}.png"
            try:
                exists = (self.root / filename).is_file()
            except OSError:
                exists = False
            if not exists:
                self._entries.pop(digest, None)
                removed += 1
        if removed:
            self.save()
        return removed


__all__ = ["CoverCache", "COVER_CACHE_DIR", "MANIFEST_NAME", "is_valid_image_bytes"]
