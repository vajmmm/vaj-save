"""ROM discovery and matching for the cartridge platforms (GBA/NDS).

Matching is done entirely against ROM files.  The save payload is never opened:
the resolver only reads the save *name* (via :func:`~.naming.save_hint`) to find
candidate ROMs, then hashes the ROM and parses its header.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

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
from .naming import (
    FUZZY_MIN_JACCARD,
    display_name_from_stem,
    extract_region,
    fuzzy_token_match,
    normalize_title,
    save_hint,
    strip_extension,
    title_tokens,
)

# Cartridge extensions per platform.  GBA dumps occasionally use ``.agb``.
ROM_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "gba": (".gba", ".agb"),
    "nds": (".nds",),
}

# Directories that must never be walked when a whole volume is used as a ROM root.
_SKIP_DIR_NAMES = frozenset(
    {
        "$recycle.bin",
        "system volume information",
        ".git",
        ".trashes",
        "node_modules",
    }
)
_MAX_WALK_DEPTH = 6

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
    # Precomputed so the fuzzy stage never re-normalises the whole ROM pool.
    tokens: frozenset = frozenset()


def make_rom_file(path, platform: str) -> RomFile:
    p = Path(path)
    stem = strip_extension(p.name)
    normalized = normalize_title(stem)
    return RomFile(
        path=p,
        platform=platform,
        stem=stem,
        normalized=normalized,
        display_name=display_name_from_stem(stem),
        region=extract_region(p.name),
        tokens=frozenset(normalized.split()),
    )


def supported_extensions(platform: str) -> Tuple[str, ...]:
    """Extensions accepted for ``platform``; empty when it is unconstrained."""
    return ROM_EXTENSIONS.get((platform or "").strip().lower(), ())


def is_supported_rom_path(path, platform: str) -> bool:
    """Whether ``path`` carries an extension accepted for ``platform``.

    Platforms without a declared constraint accept any path so the generic
    resolver keeps working for non-cartridge targets; cartridge platforms only
    accept their declared dumps (e.g. GBA: ``.gba``/``.agb``, NDS: ``.nds``).
    """
    exts = supported_extensions(platform)
    if not exts:
        return True
    return Path(path).suffix.lower() in exts


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


def build_identity_from_rom(rom: RomFile, platform: str, cache=None) -> Optional[GameIdentity]:
    """Hash ``rom`` and build a :class:`GameIdentity`, or ``None`` if unreadable.

    When a :class:`~.cache.RomIdentityCache` is supplied an unchanged ROM is
    served from the cache, skipping the whole-file digest entirely.
    """
    if cache is not None:
        cached = cache.get(rom.path, platform)
        if cached is not None:
            return cached
    try:
        sha1, crc32 = digest_file(rom.path)
    except (OSError, ValueError):
        return None
    header = read_rom_header(rom.path, platform)
    title = header.get("title") or rom.display_name or rom.stem
    identity = GameIdentity(
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
    if cache is not None:
        cache.put(rom.path, platform, identity)
    return identity


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


def _dedupe_identities(identities: Iterable[GameIdentity]) -> List[GameIdentity]:
    """Collapse identities that describe the very same game.

    The same ROM content (same ``identity_key``) can legitimately live under
    several catalogue folders, and the same save may be reachable through more
    than one scanned path.  Those are one game, not an ambiguity; only genuinely
    different content (a different ``identity_key``) stays a candidate.
    """
    out: List[GameIdentity] = []
    seen: Set[str] = set()
    for identity in identities:
        key = identity.identity_key
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(identity)
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
                root_depth = len(base.resolve().parts) if base.exists() else len(base.parts)
                for dirpath, dirnames, filenames in os.walk(base):
                    current = Path(dirpath)
                    dirnames[:] = [
                        name
                        for name in dirnames
                        if name.lower() not in _SKIP_DIR_NAMES
                        and not (current / name).is_symlink()
                    ]
                    try:
                        depth = len(current.resolve().parts) - root_depth
                    except OSError:
                        depth = len(current.parts) - len(base.parts)
                    if depth >= _MAX_WALK_DEPTH:
                        dirnames[:] = []
                    for filename in filenames:
                        if filename.lower().endswith(exts):
                            out.append(make_rom_file(current / filename, platform))
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

    def pool(self, platform: str, extra_dirs: Sequence = ()) -> List[RomFile]:
        """Configured (cached) ROMs plus a live shallow scan of ``extra_dirs``."""
        files = list(self._configured(platform))
        for directory in extra_dirs:
            files.extend(self._scan_dir(directory, platform, False))
        return _dedupe(files)

    def find(self, platform: str, hint: str, extra_dirs: Sequence = ()) -> List[RomFile]:
        normalized = normalize_title(hint)
        if not normalized:
            return []
        pool = self.pool(platform, extra_dirs)
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
    """Shared GBA/NDS resolution: manual binding > exact ROM > fuzzy ROM > binding."""
    bound = ctx.bindings.get(entry)
    if bound is not None and bound.source == SOURCE_MANUAL:
        return resolved(bound, reason="手动绑定的游戏身份", save_path=entry.path)

    hint = save_hint(entry)
    extra_dirs = sibling_dirs(entry)
    cache = getattr(ctx, "rom_cache", None)

    # Stage 1: exact normalised-name match has the highest priority.
    exact = _dedupe_identities(
        _build_identities(ctx.rom_index.find(platform, hint, extra_dirs), platform, cache)
    )
    if len(exact) > 1:
        return ambiguous(tuple(exact), reason="匹配到多个 ROM", save_path=entry.path)
    if len(exact) == 1:
        return _bind_and_resolve(exact[0], entry, ctx)

    # Stage 2: token Jaccard over the whole ROM pool.  Only a single candidate
    # that clears ``FUZZY_MIN_JACCARD`` is auto-matched; anything else stays
    # ambiguous/unresolved rather than guessing.
    fuzzy = _dedupe_identities(
        _build_identities(
            _fuzzy_candidates(ctx.rom_index.pool(platform, extra_dirs), hint), platform, cache
        )
    )
    if len(fuzzy) > 1:
        return ambiguous(tuple(fuzzy), reason="模糊匹配到多个 ROM", save_path=entry.path)
    if len(fuzzy) == 1:
        return _bind_and_resolve(fuzzy[0], entry, ctx, reason="名称近似匹配到唯一 ROM")

    if bound is not None:
        return resolved(bound, reason="未找到 ROM，使用已保存的身份绑定", save_path=entry.path)
    return unresolved(reason="未找到匹配的 ROM", save_path=entry.path)


def _build_identities(roms: Iterable[RomFile], platform: str, cache) -> List[GameIdentity]:
    matched: List[GameIdentity] = []
    for rom in roms:
        identity = build_identity_from_rom(rom, platform, cache)
        if identity is not None:
            matched.append(identity)
    return matched


def _fuzzy_candidates(pool: Iterable[RomFile], hint: str) -> List[RomFile]:
    hint_tokens = title_tokens(hint)
    if not hint_tokens:
        return []
    matches = [
        rom
        for rom in pool
        if fuzzy_token_match(
            hint_tokens,
            rom.tokens or frozenset(rom.normalized.split()),
            min_jaccard=FUZZY_MIN_JACCARD,
        )
    ]
    return sorted(matches, key=lambda rom: str(rom.path))


def _bind_and_resolve(identity: GameIdentity, entry, ctx, *, reason: str = "") -> GameIdentityResult:
    if getattr(ctx, "auto_bind", True):
        ctx.bindings.set(entry, identity, manual=False)
    return resolved(identity, reason=reason, save_path=entry.path)
