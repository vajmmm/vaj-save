"""Persistent cache of resolved :class:`GameMetadata`.

Looking a ROM up in the index is cheap once the index is in memory, but the
index itself has to be parsed and the metadata is only ever keyed by a digest we
already computed.  This cache turns the resolved record into a small JSON
document so a second launch (or a cache hit within a session) serves metadata
**without touching the provider at all**.

The store is best-effort: a missing/corrupt file degrades to an empty cache and
is rewritten on the next successful lookup.  Keys are ``platform:sha1:<...>``
with a ``platform:crc32:<...>`` fallback, so a record is reachable even when
only the weaker digest is known.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .models import GameMetadata

METADATA_CACHE_NAME = "metadata_cache.json"
_VERSION = 1


def metadata_key(
    platform: str,
    *,
    sha1: Optional[str] = None,
    crc32: Optional[str] = None,
) -> Optional[str]:
    """Primary cache key for a metadata record, or ``None`` without a digest."""
    plat = (platform or "").strip().lower()
    if not plat:
        return None
    if sha1:
        return f"{plat}:sha1:{str(sha1).strip().lower()}"
    if crc32:
        return f"{plat}:crc32:{str(crc32).strip().lower()}"
    return None


class MetadataCache:
    """A JSON-backed map of metadata keys to :class:`GameMetadata`."""

    def __init__(self, path: Optional[Union[Path, str]] = None) -> None:
        self.path: Optional[Path] = Path(path).expanduser() if path else None
        self._entries: Dict[str, Dict[str, Any]] = {}
        if self.path is not None:
            self.load()

    def get(
        self,
        platform: str,
        *,
        sha1: Optional[str] = None,
        crc32: Optional[str] = None,
    ) -> Optional[GameMetadata]:
        """Return a cached record for either digest, or ``None`` on a miss."""
        for key in (
            metadata_key(platform, sha1=sha1),
            metadata_key(platform, crc32=crc32),
        ):
            if not key:
                continue
            record = self._entries.get(key)
            if isinstance(record, dict):
                try:
                    return GameMetadata.from_dict(record)
                except Exception:  # noqa: BLE001 - a bad record must not break lookup
                    continue
        return None

    def put(self, metadata: GameMetadata) -> None:
        """Store ``metadata`` under both available digest keys (dirty, no write)."""
        keys = {
            metadata_key(metadata.platform, sha1=metadata.rom_sha1),
            metadata_key(metadata.platform, crc32=metadata.rom_crc32),
        }
        changed = False
        for key in keys:
            if not key:
                continue
            record = metadata.to_dict()
            if self._entries.get(key) != record:
                self._entries[key] = record
                changed = True
        if changed:
            self.save()

    def all(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._entries.items()}

    def load(self) -> None:
        entries: Dict[str, Dict[str, Any]] = {}
        if self.path is not None:
            try:
                if self.path.is_file():
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
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
        if self.path is None:
            return False
        payload = {"version": _VERSION, "entries": dict(self._entries)}
        tmp = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except (OSError, TypeError, ValueError):
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        return True


__all__ = ["MetadataCache", "METADATA_CACHE_NAME", "metadata_key"]
