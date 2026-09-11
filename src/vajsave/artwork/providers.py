"""Artwork providers and the official libretro thumbnail URL rules.

A provider knows, for one platform + title, the *filename* the artwork is stored
under and the *URL* to fetch it from.  Providers are layered: the service walks
an ordered list and uses the first one that supports the platform, so adding a
second source later is a one-line registration rather than a rewrite.

The only shipped provider is :class:`LibretroArtworkProvider`, which follows the
libretro thumbnail server convention:

    https://thumbnails.libretro.com/<System>/Named_Boxarts/<filename>.png

where ``<System>`` is the libretro system name and ``<filename>`` is the
No-Intro game name with the reserved characters (``&*/:`<>?\\|``) replaced by
underscores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

LIBRETRO_THUMBNAIL_BASE = "https://thumbnails.libretro.com"

# libretro system folder names for the two digest-identified platforms.
LIBRETRO_SYSTEM_NAMES = {
    "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS",
}

# libretro's canonical thumbnail sub-directories.
THUMBNAIL_BOXART = "Named_Boxarts"
THUMBNAIL_SNAP = "Named_Snaps"
THUMBNAIL_TITLE = "Named_Titles"

# Characters libretro's thumbnailer rewrites to ``_`` in a file name.
_RESERVED_FILENAME_CHARS = frozenset("&*/:`<>?\\|")


def sanitize_libretro_filename(name: str) -> Optional[str]:
    """Apply libretro's filename rule: reserved chars -> ``_``, trimmed.

    Returns ``None`` when the result is empty so callers can treat "no usable
    name" as "no artwork".
    """
    if not name:
        return None
    cleaned = "".join(
        "_" if ch in _RESERVED_FILENAME_CHARS else ch for ch in str(name)
    ).strip()
    cleaned = cleaned.strip(".")
    return cleaned or None


@dataclass(frozen=True)
class ArtworkRef:
    """A resolved, ready-to-fetch artwork location."""

    url: str
    filename: str
    provider: str
    platform: str


class ArtworkProvider:
    """Base class for an artwork source."""

    name = "base"

    def supports(self, platform: str) -> bool:  # pragma: no cover - interface
        return False

    def filename_for(self, title: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError

    def url_for(self, platform: str, filename: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError

    def ref_for(self, platform: str, title: str) -> Optional[ArtworkRef]:
        """Build a :class:`ArtworkRef`, or ``None`` when unsupported/unusable."""
        plat = (platform or "").strip().lower()
        if not self.supports(plat):
            return None
        filename = self.filename_for(title)
        if not filename:
            return None
        url = self.url_for(plat, filename)
        if not url:
            return None
        return ArtworkRef(
            url=url, filename=filename, provider=self.name, platform=plat
        )


class LibretroArtworkProvider(ArtworkProvider):
    """The official libretro thumbnail server."""

    name = "libretro"

    def __init__(self, base_url: str = LIBRETRO_THUMBNAIL_BASE) -> None:
        self.base_url = base_url.rstrip("/")

    def supports(self, platform: str) -> bool:
        return (platform or "").strip().lower() in LIBRETRO_SYSTEM_NAMES

    def filename_for(self, title: str) -> Optional[str]:
        return sanitize_libretro_filename(title)

    def url_for(self, platform: str, filename: str) -> Optional[str]:
        system = LIBRETRO_SYSTEM_NAMES.get((platform or "").strip().lower())
        if not system or not filename:
            return None
        # The official layout puts box art under ``Named_Boxarts``. Encode both
        # path segments so spaces/non-ASCII always produce a well-formed URL that
        # ``urllib`` accepts.
        system_encoded = quote(system, safe="")
        encoded = quote(filename, safe="()[]{}!-_.~,'")
        return f"{self.base_url}/{system_encoded}/{THUMBNAIL_BOXART}/{encoded}.png"


__all__ = [
    "ArtworkProvider",
    "ArtworkRef",
    "LibretroArtworkProvider",
    "LIBRETRO_THUMBNAIL_BASE",
    "LIBRETRO_SYSTEM_NAMES",
    "THUMBNAIL_BOXART",
    "THUMBNAIL_SNAP",
    "THUMBNAIL_TITLE",
    "sanitize_libretro_filename",
]
