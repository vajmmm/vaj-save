"""Persistent cache of ROM identities.

Matching a GBA/NDS save to its ROM means hashing the whole ROM file.  The UI
resolves identities on every refresh and every selection, so the same ROM would
otherwise be re-read many times per session and again after every restart.

This cache remembers the derived :class:`~.models.GameIdentity` keyed by the
ROM's resolved path plus a ``(size, mtime_ns)`` fingerprint.  A hit avoids both
the streaming digest and the header read; a changed ROM automatically misses
because its fingerprint no longer matches.

The store is JSON-backed and best-effort: a missing, corrupt or unreadable file
degrades to an empty cache and is rewritten on the next successful lookup.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from .models import GameIdentity

ROM_CACHE_NAME = "identity_rom_cache.json"
_VERSION = 1


class RomIdentityCache:
    """A small JSON-backed map of ROM path -> cached identity."""

    def __init__(self, path: Optional[Union[Path, str]] = None) -> None:
        self.path: Optional[Path] = Path(path).expanduser() if path else None
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        if self.path is not None:
            self.load()

    @staticmethod
    def key_for(path) -> str:
        try:
            return str(Path(path).resolve())
        except OSError:
            return str(path)

    @staticmethod
    def fingerprint(path) -> Optional[Tuple[int, int]]:
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        return (stat.st_size, stat.st_mtime_ns)

    def get(self, path, platform: str) -> Optional[GameIdentity]:
        """Return the cached identity for an unchanged ROM, or ``None``."""
        fingerprint = self.fingerprint(path)
        if fingerprint is None:
            return None
        key = self.key_for(path)
        with self._lock:
            record = self._entries.get(key)
        if not isinstance(record, dict) or record.get("platform") != platform:
            return None
        if (record.get("size"), record.get("mtime_ns")) != fingerprint:
            return None
        data = record.get("identity")
        if not isinstance(data, dict):
            return None
        try:
            return GameIdentity.from_dict(data)
        except Exception:  # noqa: BLE001 - a bad record must not break resolution
            return None

    def put(self, path, platform: str, identity: GameIdentity) -> None:
        """Store ``identity``; persists only when the record actually changed."""
        fingerprint = self.fingerprint(path)
        if fingerprint is None:
            return
        key = self.key_for(path)
        record = {
            "platform": platform,
            "size": fingerprint[0],
            "mtime_ns": fingerprint[1],
            "identity": identity.to_dict(),
        }
        with self._lock:
            if self._entries.get(key) == record:
                return
            self._entries[key] = record
        self.save()

    def all(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
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
        with self._lock:
            self._entries = entries

    def save(self) -> bool:
        if self.path is None:
            return False
        with self._lock:
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


__all__ = ["RomIdentityCache", "ROM_CACHE_NAME"]
