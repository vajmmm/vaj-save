"""ROM discovery and matching for the cartridge platforms (GBA/NDS).

Matching is done entirely against ROM files.  The save payload is never opened:
the resolver only reads the save *name* (via :func:`~.naming.save_hint`) to find
candidate ROMs, then hashes the ROM and parses its header.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .digest import digest_file
from .models import (
    SOURCE_MANUAL,
    SOURCE_ROM,
    GameIdentity,
    GameIdentityResult,
    ambiguous,
    resolved,
    unresolved,
)
from .naming import display_name_from_stem, extract_region, normalize_title, save_hint, strip_extension

# Cartridge extensions per platform.  GBA dumps occasionally use ``.agb``.
ROM_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "gba": (".gba", ".agb"),
    "nds": (".nds",),
}

# (title offset, game-code offset) inside a ROM header.
_HEADER_OFFSETS: Dict[str, Tuple[int, int]] = {
    "gba": (0xA0, 0xAC),
    "nds": (0x00, 0x0C),
}


@dataclass(frozen=True)
class RomFile:
    path: Path
    platform: str
    stem: str
    normalized: str
    display_name: str
    region: Optional[str]


def make_rom_file(path, platform: str) -> RomFile:
    p = Path(path)
    stem = strip_extension(p.name)
    return RomFile(
        path=p,
        platform=platform,
        stem=stem,
        normalized=normalize_title(stem),
        display_name=display_name_from_stem(stem),
        region=extract_region(p.name),
    )


def _decode_header_field(raw: bytes) -> Optional[str]:
    if not raw:
        return None
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip()
    if not text or not any(ch.isalnum() for ch in text):
        return None
    return text


def read_rom_header(path, platform: str) -> Dict[str, str]:
    """Best-effort ROM header read.  Returns ``{}`` for unreadable/short files."""
    offsets = _HEADER_OFFSETS.get(platform)
    if offsets is None:
        return {}
    title_off, code_off = offsets
    try:
        with Path(path).open("rb") as handle:
            handle.seek(title_off)
            raw_title = handle.read(12)
            handle.seek(code_off)
            raw_code = handle.read(4)
    except (OSError, ValueError):
        return {}
    data: Dict[str, str] = {}
    title = _decode_header_field(raw_title)
    code = _decode_header_field(raw_code)
    if title:
        data["title"] = title
    if code:
        data["game_code"] = code
    return data


def build_identity_from_rom(rom: RomFile, platform: str) -> Optional[GameIdentity]:
    """Hash ``rom`` and build a :class:`GameIdentity`, or ``None`` if unreadable."""
    try:
        sha1, crc32 = digest_file(rom.path)
    except (OSError, ValueError):
        return None
    header = read_rom_header(rom.path, platform)
    title = header.get("title") or rom.display_name or rom.stem
    return GameIdentity(
        identity_key=f"{platform}:sha1:{sha1}",
        platform=platform,
        title=title,
        region=rom.region,
        rom_sha1=sha1,
        rom_crc32=crc32,
        rom_path=str(rom.path),
        game_code=header.get("game_code"),
        source=SOURCE_ROM,
    )


def _dedupe(roms: Iterable[RomFile]) -> List[RomFile]:
    out: List[RomFile] = []
    seen = set()
    for rom in roms:
        try:
            key = str(rom.path.resolve())
        except OSError:
            key = str(rom.path)
        if key in seen:
            continue
        seen.add(key)
        out.append(rom)
    return out


class RomIndex:
    """A cache of ROM locations used to match saves by name.

    Configured roots are walked recursively and cached until :meth:`refresh` is
    called.  Sibling directories passed to :meth:`find` are scanned live (shallow)
    on every call so a freshly copied save/ROM pair is picked up immediately.
    """

    def __init__(self, roots: Optional[Mapping[str, Iterable]] = None) -> None:
        self._roots: Dict[str, List[Path]] = {}
        self._cache: Dict[str, List[RomFile]] = {}
        if roots:
            for platform, directories in roots.items():
                for directory in directories or ():
                    self.add_root(platform, directory)

    def add_root(self, platform: str, root) -> None:
        p = Path(root).expanduser()
        self._roots.setdefault(platform, [])
        if p not in self._roots[platform]:
            self._roots[platform].append(p)
        self._cache.pop(platform, None)

    def refresh(self) -> None:
        self._cache.clear()

    def _extensions(self, platform: str) -> Tuple[str, ...]:
        return ROM_EXTENSIONS.get(platform, ())

    def _scan_dir(self, root, platform: str, recursive: bool) -> List[RomFile]:
        exts = self._extensions(platform)
        if not exts:
            return []
        base = Path(root)
        out: List[RomFile] = []
        try:
            if recursive:
                for dirpath, dirnames, filenames in os.walk(base):
                    dirnames[:] = [
                        name for name in dirnames if not (Path(dirpath) / name).is_symlink()
                    ]
                    for filename in filenames:
                        if filename.lower().endswith(exts):
                            out.append(make_rom_file(Path(dirpath) / filename, platform))
            else:
                if not base.is_dir():
                    return []
                for child in sorted(base.iterdir()):
                    try:
                        if child.is_file() and child.suffix.lower() in exts:
                            out.append(make_rom_file(child, platform))
                    except OSError:
                        continue
        except OSError:
            return out
        return out

    def _configured(self, platform: str) -> List[RomFile]:
        cached = self._cache.get(platform)
        if cached is None:
            files: List[RomFile] = []
            for root in self._roots.get(platform, []):
                files.extend(self._scan_dir(root, platform, True))
            cached = _dedupe(files)
            self._cache[platform] = cached
        return cached

    def all_roms(self, platform: str) -> List[RomFile]:
        return list(self._configured(platform))

    def find(self, platform: str, hint: str, extra_dirs: Sequence = ()) -> List[RomFile]:
        normalized = normalize_title(hint)
        if not normalized:
            return []
        pool = list(self._configured(platform))
        for directory in extra_dirs:
            pool.extend(self._scan_dir(directory, platform, False))
        pool = _dedupe(pool)
        matches = [rom for rom in pool if rom.normalized == normalized]
        return sorted(matches, key=lambda rom: str(rom.path))


def sibling_dirs(entry) -> List[Path]:
    """Directories near the save that may hold its ROM (save dir, parent, grandparent)."""
    path = Path(getattr(entry, "path", "") or "")
    try:
        is_file = path.is_file()
    except OSError:
        is_file = bool(path.suffix)
    base = path.parent if is_file else path
    candidates = [base, base.parent, base.parent.parent]
    out: List[Path] = []
    for candidate in candidates:
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def resolve_rom_identity(entry, ctx, *, platform: str) -> GameIdentityResult:
    """Shared GBA/NDS resolution: manual binding > ROM match > stored binding."""
    bound = ctx.bindings.get(entry)
    if bound is not None and bound.source == SOURCE_MANUAL:
        return resolved(bound, reason="手动绑定的游戏身份", save_path=entry.path)

    roms = ctx.rom_index.find(platform, save_hint(entry), extra_dirs=sibling_dirs(entry))
    matched: List[GameIdentity] = []
    for rom in roms:
        identity = build_identity_from_rom(rom, platform)
        if identity is not None:
            matched.append(identity)

    if len(matched) > 1:
        return ambiguous(tuple(matched), reason="匹配到多个 ROM", save_path=entry.path)
    if len(matched) == 1:
        identity = matched[0]
        if getattr(ctx, "auto_bind", True):
            ctx.bindings.set(entry, identity, manual=False)
        return resolved(identity, save_path=entry.path)
    if bound is not None:
        return resolved(bound, reason="未找到 ROM，使用已保存的身份绑定", save_path=entry.path)
    return unresolved(reason="未找到匹配的 ROM", save_path=entry.path)
