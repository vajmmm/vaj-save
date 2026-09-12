"""Artwork providers and the official libretro thumbnail URL rules.

An :class:`ArtworkProvider` knows, for one :class:`~vajsave.metadata.GameMetadata`
record, the *filename* the artwork is stored under and the *URL* to fetch it
from.  Providers are layered: the service walks an ordered list and uses the
first one that supports the platform, so adding a second source later is a
one-line registration rather than a rewrite.

The only shipped provider is :class:`LibretroThumbnailProvider`, which follows
the libretro thumbnail server convention:

    https://thumbnails.libretro.com/<System>/Named_Boxarts/<filename>.png

where ``<System>`` is the libretro system name and ``<filename>`` is the
No-Intro canonical title with the reserved characters replaced by underscores.
The reserved set is the official libretro one, ``&*/:`<>?\\|"`` (double quote
included); every path segment is then percent-encoded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

LIBRETRO_THUMBNAIL_BASE = "https://thumbnails.libretro.com"

# libretro system folder names for the platforms the app resolves a title for.
# GBA/NDS titles come from the ROM-digest index; PSP/Vita titles come from the
# save's PARAM.SFO (see :meth:`vajsave.artwork.ArtworkService.ensure_cover_for_title`).
LIBRETRO_SYSTEM_NAMES = {
    "gba": "Nintendo - Game Boy Advance",
    "nds": "Nintendo - Nintendo DS",
    "psp": "Sony - PlayStation Portable",
    "vita": "Sony - PlayStation Vita",
}

# libretro's canonical thumbnail sub-directories.
THUMBNAIL_BOXART = "Named_Boxarts"
THUMBNAIL_SNAP = "Named_Snaps"
THUMBNAIL_TITLE = "Named_Titles"

# Characters libretro's thumbnailer rewrites to ``_`` in a file name.  This is
# the set the libretro-thumbnails repository tests for (``&*:`<>?\|"``); the
# double quote is included, so a title like ``Spider-Man "The Movie"`` never
# produces a URL that needs a quote character.
_RESERVED_FILENAME_CHARS = frozenset("&*/:`<>?\\|\"")

# Characters that stay literal in the percent-encoded URL.  Parentheses, commas
# and a few sub-delimiters are valid in a URL path and are kept as-is by the
# official thumbnail server.
_URL_SAFE = "()[]{}!-_.~,'"


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
class Artwork:
    """A resolved, ready-to-fetch artwork location."""

    url: str
    filename: str
    provider: str
    platform: str
    canonical_title: str = ""


class ArtworkProvider:
    """Base class for an artwork source."""

    name = "base"

    def supports(self, platform: str) -> bool:  # pragma: no cover - interface
        return False

    def find_cover(self, metadata) -> Optional[Artwork]:
        """Return the artwork for ``metadata``, or ``None`` when unavailable."""
        return None

    def ref_for(self, platform: str, title: str) -> Optional[Artwork]:
        """Alias for :meth:`cover_for` (kept for older call sites)."""
        return self.cover_for(platform, title)

    def cover_for(self, platform: str, title: str) -> Optional[Artwork]:
        """Build an :class:`Artwork` from a platform+title (no metadata object)."""
        plat = (platform or "").strip().lower()
        if not self.supports(plat):
            return None
        filename = self.filename_for(title)
        if not filename:
            return None
        url = self.url_for(plat, filename)
        if not url:
            return None
        return Artwork(
            url=url,
            filename=filename,
            provider=self.name,
            platform=plat,
            canonical_title=str(title or ""),
        )

    def filename_for(self, title: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError

    def url_for(self, platform: str, filename: str) -> Optional[str]:  # pragma: no cover
        raise NotImplementedError


class LibretroThumbnailProvider(ArtworkProvider):
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
        encoded = quote(filename, safe=_URL_SAFE)
        return f"{self.base_url}/{system_encoded}/{THUMBNAIL_BOXART}/{encoded}.png"

    def find_cover(self, metadata) -> Optional[Artwork]:
        if metadata is None:
            return None
        platform = str(getattr(metadata, "platform", "") or "")
        title = str(getattr(metadata, "canonical_title", "") or "")
        if not title:
            return None
        return self.cover_for(platform, title)


__all__ = [
    "Artwork",
    "ArtworkProvider",
    "LibretroThumbnailProvider",
    "LIBRETRO_THUMBNAIL_BASE",
    "LIBRETRO_SYSTEM_NAMES",
    "THUMBNAIL_BOXART",
    "THUMBNAIL_SNAP",
    "THUMBNAIL_TITLE",
    "sanitize_libretro_filename",
]
