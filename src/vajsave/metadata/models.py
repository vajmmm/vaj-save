"""Value object for the ``GameMetadata`` layer.

Metadata is deliberately *separate* from :class:`~vajsave.identity.GameIdentity`:

* ``GameIdentity`` answers "which game is this save, and how do we group it?"
  (a stable key derived from the ROM digest or a title id);
* ``GameMetadata`` answers "what is the canonical name/region of the ROM that
  digest belongs to?" (a descriptive record looked up in an offline index).

The two layers never replace one another: a save can have a perfectly good
identity while its ROM is absent from the metadata index (``metadata is None``),
or carry metadata that never becomes an identity.  Keeping the record frozen and
dependency-free means it can be cached as JSON and passed across the UI boundary
without dragging the identity layer along.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

# Source tag for metadata that came from a local libretro / No-Intro index.
SOURCE_LIBRETRO = "libretro"


@dataclass(frozen=True)
class GameMetadata:
    """A canonical description of one ROM, keyed by its digest.

    ``canonical_title`` is the index's own game name (region tags included, e.g.
    ``"Pokemon - FireRed Version (USA)"``); ``region`` is the canonicalised
    region extracted from it (``"USA"``).  ``rom_sha1`` / ``rom_crc32`` are the
    *keys* the record was found by, stored so the metadata survives a cache
    round-trip without re-deriving them.
    """

    canonical_title: str
    platform: str
    region: Optional[str] = None
    source: str = SOURCE_LIBRETRO
    rom_sha1: Optional[str] = None
    rom_crc32: Optional[str] = None

    @property
    def display_title(self) -> str:
        """Title for the UI; falls back to the raw canonical title."""
        return self.canonical_title or ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "canonical_title": self.canonical_title,
            "platform": self.platform,
            "source": self.source,
        }
        for key in ("region", "rom_sha1", "rom_crc32"):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameMetadata":
        return cls(
            canonical_title=data.get("canonical_title") or "",
            platform=data.get("platform") or "",
            region=data.get("region"),
            source=data.get("source") or SOURCE_LIBRETRO,
            rom_sha1=data.get("rom_sha1"),
            rom_crc32=data.get("rom_crc32"),
        )


__all__ = ["GameMetadata", "SOURCE_LIBRETRO"]
