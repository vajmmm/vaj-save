"""NDS identity resolver: match the save name against NDS ROMs."""

from __future__ import annotations

from ..rom_formats import supported_extensions
from .models import GameIdentityResult
from .roms import resolve_rom_identity

PLATFORM = "nds"
EXTENSIONS = supported_extensions(PLATFORM)


def resolve(entry, ctx) -> GameIdentityResult:
    return resolve_rom_identity(entry, ctx, platform=PLATFORM)
