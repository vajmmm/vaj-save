"""Value objects for the independent ``GameIdentity`` layer.

The identity layer is deliberately decoupled from the scanner and the backup
library: a :class:`GameIdentity` describes *which game* a save belongs to in a
way that survives renames and moves, and a :class:`GameIdentityResult` records
how confident the resolver is about that answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# Confidence states returned by a resolver.  ``resolved`` and ``unresolved`` are
# terminal; ``partial`` (some metadata is known) and ``ambiguous`` (more than one
# candidate) still carry useful information for the caller.
STATUS_RESOLVED = "resolved"
STATUS_PARTIAL = "partial"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNRESOLVED = "unresolved"
STATUSES = (STATUS_RESOLVED, STATUS_PARTIAL, STATUS_AMBIGUOUS, STATUS_UNRESOLVED)

# How an identity was obtained.  ``manual`` bindings take precedence over any
# automatic match because the user asked for them explicitly.
SOURCE_ROM = "rom"
SOURCE_BINDING = "binding"
SOURCE_MANUAL = "manual"
SOURCE_METADATA = "metadata"
SOURCE_SFO = "sfo"
SOURCE_FILENAME = "filename"


@dataclass(frozen=True)
class GameIdentity:
    """A stable, path-independent identity for one game.

    ``identity_key`` is the canonical grouping key:

    * GBA/NDS  -> ``"<platform>:sha1:<hex>"`` (digest of the matched ROM)
    * PSP/Vita/3DS/Switch -> ``"<platform>:<title_id>"``
    * name-only fallback  -> ``"<platform>:name:<normalized title>"``
    """

    identity_key: str
    platform: str
    title: str
    title_id: Optional[str] = None
    region: Optional[str] = None
    rom_sha1: Optional[str] = None
    rom_crc32: Optional[str] = None
    rom_path: Optional[str] = None
    game_code: Optional[str] = None
    source: str = SOURCE_METADATA

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "identity_key": self.identity_key,
            "platform": self.platform,
            "title": self.title,
            "source": self.source,
        }
        for key in ("title_id", "region", "rom_sha1", "rom_crc32", "rom_path", "game_code"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameIdentity":
        return cls(
            identity_key=data.get("identity_key") or "",
            platform=data.get("platform") or "",
            title=data.get("title") or "",
            title_id=data.get("title_id"),
            region=data.get("region"),
            rom_sha1=data.get("rom_sha1"),
            rom_crc32=data.get("rom_crc32"),
            rom_path=data.get("rom_path"),
            game_code=data.get("game_code"),
            source=data.get("source") or SOURCE_METADATA,
        )


@dataclass(frozen=True)
class GameIdentityResult:
    """Outcome of resolving one :class:`~vajsave.models.SaveEntry`."""

    status: str
    identity: Optional[GameIdentity] = None
    candidates: Tuple[GameIdentity, ...] = ()
    reason: str = ""
    save_path: Optional[str] = None

    @property
    def is_resolved(self) -> bool:
        return self.status == STATUS_RESOLVED

    @property
    def identity_key(self) -> Optional[str]:
        return self.identity.identity_key if self.identity is not None else None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"status": self.status}
        if self.identity is not None:
            data["identity"] = self.identity.to_dict()
        if self.candidates:
            data["candidates"] = [c.to_dict() for c in self.candidates]
        if self.reason:
            data["reason"] = self.reason
        if self.save_path is not None:
            data["save_path"] = self.save_path
        return data


def resolved(
    identity: GameIdentity,
    *,
    reason: str = "",
    save_path: Optional[str] = None,
) -> GameIdentityResult:
    return GameIdentityResult(
        status=STATUS_RESOLVED, identity=identity, reason=reason, save_path=save_path
    )


def partial(
    identity: GameIdentity,
    *,
    reason: str = "",
    save_path: Optional[str] = None,
) -> GameIdentityResult:
    return GameIdentityResult(
        status=STATUS_PARTIAL, identity=identity, reason=reason, save_path=save_path
    )


def ambiguous(
    candidates: Tuple[GameIdentity, ...],
    *,
    reason: str = "",
    save_path: Optional[str] = None,
) -> GameIdentityResult:
    return GameIdentityResult(
        status=STATUS_AMBIGUOUS,
        candidates=tuple(candidates),
        reason=reason,
        save_path=save_path,
    )


def unresolved(*, reason: str = "", save_path: Optional[str] = None) -> GameIdentityResult:
    return GameIdentityResult(status=STATUS_UNRESOLVED, reason=reason, save_path=save_path)
