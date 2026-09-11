"""Persistent bindings between a scanned save and a resolved game identity.

A binding is keyed by ``<platform>:<normalized save name>`` -- deliberately
*not* by the absolute save path -- so moving or renaming the save directory
keeps matching.  Manual bindings outrank automatic ROM matches.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Optional, Union

from .models import GameIdentity, SOURCE_BINDING, SOURCE_MANUAL
from .naming import normalize_title, save_hint

BINDINGS_NAME = "identity_bindings.json"
_VERSION = 1


class BindingStore:
    """A small JSON-backed map of stable save keys to game identities.

    When ``path`` is ``None`` the store is in-memory only (handy for tests and
    one-shot resolution).  Writes are atomic: a temp file is renamed over the
    target, mirroring ``vajsave.library.save_app_config``.
    """

    def __init__(self, path: Optional[Union[Path, str]] = None) -> None:
        self.path: Optional[Path] = Path(path).expanduser() if path else None
        self._data: Dict[str, Dict[str, Any]] = {}
        if self.path is not None:
            self.load()

    @staticmethod
    def key_for(entry) -> str:
        platform = (getattr(entry, "platform", "") or "unknown").strip().lower() or "unknown"
        return f"{platform}:{normalize_title(save_hint(entry))}"

    def get_record(self, entry) -> Optional[Dict[str, Any]]:
        return self._data.get(self.key_for(entry))

    def get(self, entry) -> Optional[GameIdentity]:
        record = self.get_record(entry)
        if not record:
            return None
        raw = record.get("identity")
        if not isinstance(raw, dict):
            return None
        identity = GameIdentity.from_dict(raw)
        kind = record.get("kind") or SOURCE_BINDING
        return replace(identity, source=kind)

    def set(self, entry, identity: GameIdentity, *, manual: bool = False) -> GameIdentity:
        kind = SOURCE_MANUAL if manual else SOURCE_BINDING
        stored = replace(identity, source=kind)
        self._data[self.key_for(entry)] = {
            "identity": stored.to_dict(),
            "kind": kind,
            "hint": save_hint(entry),
        }
        self.save()
        return stored

    def remove(self, entry) -> bool:
        removed = self._data.pop(self.key_for(entry), None) is not None
        if removed:
            self.save()
        return removed

    def all(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(value) for key, value in self._data.items()}

    def load(self) -> None:
        self._data = {}
        if self.path is None:
            return
        try:
            if not self.path.is_file():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        if not isinstance(raw, dict):
            return
        bindings = raw.get("bindings")
        if isinstance(bindings, dict):
            self._data = {
                key: value for key, value in bindings.items() if isinstance(value, dict)
            }

    def save(self) -> bool:
        if self.path is None:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            payload = {"version": _VERSION, "bindings": self._data}
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        except (OSError, TypeError, ValueError):
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True
