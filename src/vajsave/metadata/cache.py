"""Persistent cache of resolved :class:`GameMetadata`, keyed by ``identity_key``.

Looking a ROM up in the index is cheap once the index is in memory, but the
index still has to be parsed and the result is stable for a given game
identity.  This cache turns the resolved record into a small JSON document so a
second launch (or a cache hit within a session) serves metadata **without
touching the provider at all**.

Keying is by :attr:`GameIdentity.identity_key` -- the stable, path-independent
grouping key -- so a cached record is reusable across renames, moves and slots.

The store is thread-safe (all mutations under one ``Lock``) and atomic (a temp
file is renamed into place), and best-effort: a missing/corrupt file degrades to
an empty cache and is rewritten on the next successful lookup.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .models import GameMetadata

# Name of the persisted cache under ``<library_root>``.
METADATA_CACHE_NAME = "game_metadata.json"
_VERSION = 1


class MetadataCache:
    """A JSON-backed map of ``identity_key`` -> :class:`GameMetadata`."""

    def __init__(self, path: Optional[Union[Path, str]] = None) -> None:
        self.path: Optional[Path] = Path(path).expanduser() if path else None
        self._lock = threading.Lock()
        self._entries: Dict[str, Dict[str, Any]] = {}
        if self.path is not None:
            self.load()

    def get(self, identity_key: Optional[str]) -> Optional[GameMetadata]:
        """Return the cached record for ``identity_key``, or ``None``."""
        if not identity_key:
            return None
        with self._lock:
            record = self._entries.get(str(identity_key))
            if not isinstance(record, dict):
                return None
            try:
                return GameMetadata.from_dict(record)
            except Exception:  # noqa: BLE001 - a bad record must not break lookup
                return None

    def put(self, metadata: GameMetadata) -> bool:
        """Store ``metadata`` under its ``identity_key`` and persist atomically.

        Returns ``True`` when the cache changed.
        """
        key = getattr(metadata, "identity_key", "") or ""
        if not key:
            return False
        record = metadata.to_dict()
        # Build the snapshot *and* write it under the lock so two concurrent
        # puts can never interleave into a stale on-disk document.
        with self._lock:
            if self._entries.get(key) == record:
                return False
            self._entries[key] = record
            payload = {"version": _VERSION, "entries": dict(self._entries)}
            return self._write(payload)

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
        with self._lock:
            if self.path is None:
                return False
            payload = {"version": _VERSION, "entries": dict(self._entries)}
            return self._write(payload)

    def _write(self, payload: Dict[str, Any]) -> bool:
        path = self.path
        if path is None:
            return False
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
        except (OSError, TypeError, ValueError):
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            return False
        return True


__all__ = ["MetadataCache", "METADATA_CACHE_NAME"]
