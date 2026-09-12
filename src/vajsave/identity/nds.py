"""NDS identity resolver: match the save name against NDS ROMs."""

from __future__ import annotations

from .models import GameIdentityResult
from .roms import ROM_EXTENSIONS, resolve_rom_identity

PLATFORM = "nds"
EXTENSIONS = ROM_EXTENSIONS["nds"]


def resolve(entry, ctx) -> GameIdentityResult:
    return resolve_rom_identity(entry, ctx, platform=PLATFORM)
