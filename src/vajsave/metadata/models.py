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

Schema (frozen, format version 1)::

    GameMetadata(
        identity_key: str,               # the GameIdentity it was resolved from
        platform: str,                   # "gba" / "nds"
        canonical_title: str,            # No-Intro game name, region tags included
        region: str | None,              # canonicalised region, e.g. "USA"
        external_ids: dict[str, str] | None,  # e.g. {"serial": "BPEE", "nointro": "1961"}
        source: str,                     # provenance tag, e.g. "libretro"
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

# Source tag for metadata that came from a local libretro / No-Intro index.
SOURCE_LIBRETRO = "libretro"


@dataclass(frozen=True)
class GameMetadata:
    """A canonical description of one ROM, keyed by its game identity.

    ``canonical_title`` is the index's own game name (region tags included, e.g.
    ``"Pokemon - FireRed Version (USA)"``); ``region`` is the canonicalised
    region extracted from it (``"USA"``).  ``external_ids`` carries the
    identifiers the source database exposes (No-Intro's own id and the
    cartridge serial), which are descriptive only -- the *grouping* key stays
    :attr:`identity_key`.
    """

    identity_key: str
    platform: str
    canonical_title: str
    region: Optional[str] = None
    external_ids: Optional[Dict[str, str]] = None
    source: str = SOURCE_LIBRETRO

    @property
    def display_title(self) -> str:
        """Title for the UI; falls back to an empty string."""
        return self.canonical_title or ""

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "identity_key": self.identity_key,
            "platform": self.platform,
            "canonical_title": self.canonical_title,
            "source": self.source,
        }
        if self.region is not None:
            data["region"] = self.region
        if self.external_ids:
            data["external_ids"] = dict(self.external_ids)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "GameMetadata":
        raw_ids = data.get("external_ids")
        external_ids: Optional[Dict[str, str]] = None
        if isinstance(raw_ids, Mapping):
            external_ids = {str(k): str(v) for k, v in raw_ids.items()}
        return cls(
            identity_key=str(data.get("identity_key") or ""),
            platform=str(data.get("platform") or ""),
            canonical_title=str(data.get("canonical_title") or ""),
            region=data.get("region"),
            external_ids=external_ids,
            source=data.get("source") or SOURCE_LIBRETRO,
        )


__all__ = ["GameMetadata", "SOURCE_LIBRETRO"]
