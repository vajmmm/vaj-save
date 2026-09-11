"""GBA identity resolver: match the save name against ``.gba``/``.agb`` ROMs."""

from __future__ import annotations

from .models import GameIdentityResult
from .roms import resolve_rom_identity

PLATFORM = "gba"
EXTENSIONS = (".gba", ".agb")


def resolve(entry, ctx) -> GameIdentityResult:
    return resolve_rom_identity(entry, ctx, platform=PLATFORM)
