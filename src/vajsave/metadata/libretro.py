"""Offline libretro / No-Intro index parsing and hash lookup.

The index is the *provider* side of the metadata layer: a local
``Logiqx``-style XML datafile (the format libretro and No-Intro ship) is parsed
**once** into two in-memory maps, so a hash lookup never re-reads or re-parses
the ``.dat`` files.  Lookups are keyed by SHA-1 (preferred) and CRC32.

Only cartridge platforms the app actually identifies by ROM digest (GBA/NDS)
are indexed; entries for any other platform are ignored so a lookup for an
unsupported platform can only ever return ``None``.  Malformed, missing or
unreadable files degrade to "nothing indexed" rather than raising.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Union

from ..identity.naming import extract_region
from .models import GameMetadata

# Platforms the metadata layer can answer for.  Everything else is out of scope
# by design: the other consoles are identified by title id, not ROM digest.
SUPPORTED_PLATFORMS: Tuple[str, ...] = ("gba", "nds")

# Substrings found in a datafile header/name that map to a supported platform.
# Matched case-insensitively, longest/most specific first.
_PLATFORM_MARKERS: Tuple[Tuple[str, str], ...] = (
    ("game boy advance", "gba"),
    ("gameboy advance", "gba"),
    ("nintendo ds", "nds"),
    ("nintendo - ds", "nds"),
    ("gba", "gba"),
    ("nds", "nds"),
)


def _normalize_hex(value: Optional[str], width: int) -> Optional[str]:
    """Canonicalise a hex digest: lowercase, no ``0x``/separators, zero-padded.

    Returns ``None`` for a missing or non-hex value so a bad field is simply not
    indexed instead of poisoning the map with a bogus key.
    """
    if value is None:
        return None
    text = str(value).strip().lower().replace("0x", "")
    if not text:
        return None
    try:
        number = int(text, 16)
    except ValueError:
        return None
    if number < 0:
        return None
    return format(number, "0{}x".format(width))


def detect_platform(*names: Optional[str]) -> Optional[str]:
    """Best-effort platform from a datafile header/filename (or ``None``)."""
    for name in names:
        if not name:
            continue
        haystack = str(name).strip().lower()
        for marker, platform in _PLATFORM_MARKERS:
            if marker in haystack:
                return platform
    return None


class LibretroIndex:
    """An in-memory ``SHA-1``/``CRC32`` -> :class:`GameMetadata` lookup."""

    def __init__(self) -> None:
        self._by_sha1: Dict[str, GameMetadata] = {}
        self._by_crc: Dict[str, GameMetadata] = {}
        self._games: int = 0

    # -- building ------------------------------------------------------------

    def add(self, metadata: GameMetadata) -> None:
        if metadata.platform not in SUPPORTED_PLATFORMS:
            return
        if metadata.rom_sha1:
            self._by_sha1[metadata.rom_sha1] = metadata
        if metadata.rom_crc32:
            self._by_crc[metadata.rom_crc32] = metadata
        self._games += 1

    def load_file(self, path: Union[Path, str], *, platform: Optional[str] = None) -> int:
        """Parse one datafile and add its games. Returns the number of games added.

        ``platform`` overrides header/filename detection; when neither yields a
        supported platform the file contributes nothing.
        """
        p = Path(path)
        try:
            tree = ET.parse(p)
        except (OSError, ET.ParseError, ValueError):
            return 0
        root = tree.getroot()
        if root is None:
            return 0
        header = root.find("header")
        header_names: List[str] = []
        if header is not None:
            for tag in ("name", "description"):
                node = header.find(tag)
                if node is not None and node.text:
                    header_names.append(node.text)
        resolved = (
            (platform or "").strip().lower()
            or detect_platform(*header_names, p.name)
        )
        if resolved not in SUPPORTED_PLATFORMS:
            return 0
        added = 0
        for game in list(root):
            if game.tag not in ("game", "machine"):
                continue
            name = (game.get("name") or "").strip()
            if not name:
                description = game.find("description")
                name = (description.text or "").strip() if description is not None else ""
            if not name:
                continue
            region = extract_region(name)
            for rom in game.findall("rom"):
                sha1 = _normalize_hex(rom.get("sha1"), 40)
                crc32 = _normalize_hex(rom.get("crc"), 8)
                if not sha1 and not crc32:
                    continue
                self.add(
                    GameMetadata(
                        canonical_title=name,
                        platform=resolved,
                        region=region,
                        rom_sha1=sha1,
                        rom_crc32=crc32,
                    )
                )
                added += 1
        return added

    def load_directory(
        self,
        directory: Union[Path, str],
        *,
        platform: Optional[str] = None,
        patterns: Iterable[str] = ("*.dat", "*.xml"),
    ) -> int:
        """Load every matching datafile under ``directory`` (non-recursive)."""
        base = Path(directory)
        try:
            if not base.is_dir():
                return 0
        except OSError:
            return 0
        total = 0
        seen: set = set()
        for pattern in patterns:
            for candidate in sorted(base.glob(pattern)):
                try:
                    key = str(candidate.resolve())
                except OSError:
                    key = str(candidate)
                if key in seen:
                    continue
                seen.add(key)
                total += self.load_file(candidate, platform=platform)
        return total

    @classmethod
    def from_paths(
        cls,
        paths: Iterable[Union[Path, str]],
        *,
        platform: Optional[str] = None,
    ) -> "LibretroIndex":
        index = cls()
        for path in paths:
            p = Path(path)
            try:
                if p.is_dir():
                    index.load_directory(p, platform=platform)
                else:
                    index.load_file(p, platform=platform)
            except OSError:
                continue
        return index

    # -- lookup --------------------------------------------------------------

    def lookup(
        self,
        *,
        platform: str,
        crc32: Optional[str] = None,
        sha1: Optional[str] = None,
    ) -> Optional[GameMetadata]:
        """Resolve a ROM by digest, or ``None`` when unknown/unsupported.

        SHA-1 is preferred over CRC32 because it is collision-resistant; a digest
        recorded under a *different* platform never satisfies the request.
        """
        wanted = (platform or "").strip().lower()
        if wanted not in SUPPORTED_PLATFORMS:
            return None
        norm_sha1 = _normalize_hex(sha1, 40) if sha1 else None
        norm_crc = _normalize_hex(crc32, 8) if crc32 else None
        for digest, table in ((norm_sha1, self._by_sha1), (norm_crc, self._by_crc)):
            if not digest:
                continue
            found = table.get(digest)
            if found is not None and found.platform == wanted:
                return found
        return None

    # -- introspection -------------------------------------------------------

    @property
    def games(self) -> int:
        return self._games

    def __len__(self) -> int:
        return self._games

    def __bool__(self) -> bool:
        return self._games > 0


__all__ = ["LibretroIndex", "SUPPORTED_PLATFORMS", "detect_platform"]
