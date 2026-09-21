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

import unicodedata
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import quote

LIBRETRO_THUMBNAIL_BASE = "https://thumbnails.libretro.com"

# libretro system folder names for the platforms the app resolves a title for.
# GB/GBC/GBA/NDS titles come from the ROM-digest index; PSP/Vita/3DS titles come
# from the save's own name (PARAM.SFO or Checkpoint/JKSM folder).
LIBRETRO_SYSTEM_NAMES = {
    "gba": "Nintendo - Game Boy Advance",
    "gb": "Nintendo - Game Boy",
    "gbc": "Nintendo - Game Boy Color",
    "nds": "Nintendo - Nintendo DS",
    "psp": "Sony - PlayStation Portable",
    "vita": "Sony - PlayStation Vita",
    "3ds": "Nintendo - Nintendo 3DS",
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


# Checkpoint / SFO titles rarely match No-Intro filenames. After the exact
# name 404s we try collapsed whitespace, ASCII-folded letters, title case for
# ALL-CAPS names, and the common region tags. Bounded so one save cannot fan
# out into an unbounded crawl.
_REGION_SUFFIXES = (" (USA)", " (Europe)", " (Japan)", " (World)")
_MAX_TITLE_CANDIDATES = 12


def _fold_ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _title_case_if_shouting(text: str) -> str:
    letters = [ch for ch in text if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        pretty = text.title()
        return pretty.replace("'S ", "'s ").replace("'S", "'s")
    return text


def libretro_title_candidates(title: str) -> Tuple[str, ...]:
    """Ordered unique titles to try against Named_Boxarts.

    The first entry is the original (stripped) name so an already-canonical
    title still hits on the first request.
    """
    original = str(title or "").strip()
    if not original:
        return ()
    ordered: list[str] = []

    def add(value: str) -> None:
        cleaned = " ".join(value.split()).strip().rstrip(".…")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)

    add(original)
    add(_fold_ascii(original))
    add(_title_case_if_shouting(original))
    add(_title_case_if_shouting(_fold_ascii(original)))
    for base in list(ordered):
        if "(" in base:
            continue
        for suffix in _REGION_SUFFIXES:
            add(base + suffix)
            if len(ordered) >= _MAX_TITLE_CANDIDATES:
                return tuple(ordered)
    return tuple(ordered[:_MAX_TITLE_CANDIDATES])


# PSP save folders often contain a short product code plus DATA/profile
# suffixes instead of the retail title used by libretro. These aliases are
# deliberately curated to real Named_Boxarts entries; an ID that is not listed
# here still follows the normal title and directory-listing fallbacks.
# Keys are normalized alphanumeric stems so ULJM05800DAT can use the same
# entry as ULJM05800 without duplicating every save-data suffix.
PSP_TITLE_ALIASES: dict[str, Tuple[str, ...]] = {
    "NPJH001420001": ("Yu-Gi-Oh! ARC-V Tag Force Special (Japan)",),
    "NPJH50040": ("Persona 3 Portable (Japan)",),
    "NPJH50045001": ("Metal Gear Solid - Peace Walker (Japan) (v1.02)",),
    "NPJH50239": ("Dead or Alive - Paradise (USA) (En,Ja,Fr,De)",),
    "NPJH50263": ("Ace Combat - Joint Assault (USA)",),
    "UCAS40063": (
        "LocoRoco (USA) (En,Ja,Fr,De,Es,It,Nl,Pt,Sv,No,Da,Fi,Zh,Ko,Ru)",
    ),
    "UCAS40193": ("Patapon (USA)",),
    "UCAS40198": ("God of War - Chains of Olympus (USA)",),
    "ULJM05101": ("Valkyrie Profile - Lenneth (USA)",),
    "ULJM05155": ("Ys - Napishtim no Hako (Japan) (v1.03) (Tokubetsuban)",),
    "ULJM05156": ("Monster Hunter Portable 2nd (Japan) (v1.01)",),
    "ULJM05254": ("Crisis Core - Final Fantasy VII (Japan, Asia)",),
    "ULJM05500": ("Monster Hunter Portable 2nd G (Japan) (v1.03)",),
    "ULJM05505": ("Ninja Katsugeki - Tenchu San Portable (Japan) (v1.01)",),
    "ULJM05600": ("Kingdom Hearts - Birth by Sleep (USA) (En,Fr,Es)",),
    "ULJM05800": ("Monster Hunter Portable 3rd (Japan) (v1.02)",),
    "ULJS00107": ("Dragon Ball Z - Shin Budokai 2 (Japan) (v1.02)",),
    "ULJS00394": ("Grand Knights History (Japan) (v1.01)",),
    "ULUS10154": ("Metal Slug Anthology (USA)",),
    "ULUS10466": ("Tekken 6 (USA) (En,Fr,De,Es,It,Ru)",),
}


def _normalize_psp_title_id(value: object) -> str:
    """Normalize a PSP product code for exact or prefix alias lookup."""
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _psp_aliases_for_id(title_id: object) -> Tuple[str, ...]:
    key = _normalize_psp_title_id(title_id)
    if not key:
        return ()
    aliases = PSP_TITLE_ALIASES.get(key)
    if aliases is not None:
        return aliases
    # Save-data directories append a stable product stem with arbitrary
    # profile/data words. Choose the longest matching stem to avoid a shorter
    # entry winning if the table later gains related products.
    prefixes = [stem for stem in PSP_TITLE_ALIASES if key.startswith(stem)]
    if not prefixes:
        return ()
    return PSP_TITLE_ALIASES[max(prefixes, key=len)]


def psp_title_candidates(title: str, title_id: Optional[str] = None) -> Tuple[str, ...]:
    """Return PSP-specific aliases followed by generic title candidates.

    A curated Title ID alias is tried first because it maps a save folder to a
    real libretro retail filename without relying on translated SFO text. The
    ordinary title candidates remain as a fallback for unlisted games and for
    users with a custom artwork provider.
    """
    ordered: list[str] = []

    def add(value: str) -> None:
        cleaned = " ".join(str(value or "").split()).strip().rstrip(".…")
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)

    for alias in _psp_aliases_for_id(title_id):
        add(alias)
    for candidate in libretro_title_candidates(title):
        add(candidate)
    return tuple(ordered)


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

    def boxart_listing_url(self, platform: str) -> Optional[str]:
        """Directory index of the boxart folder, or ``None`` when unsupported.

        Used as a last-resort fallback: when every generated candidate filename
        404s, the provider's own directory listing is consulted for the real
        file name.
        """
        return None


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

    def boxart_listing_url(self, platform: str) -> Optional[str]:
        system = LIBRETRO_SYSTEM_NAMES.get((platform or "").strip().lower())
        if not system:
            return None
        system_encoded = quote(system, safe="")
        return f"{self.base_url}/{system_encoded}/{THUMBNAIL_BOXART}/"

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
    "libretro_title_candidates",
]
