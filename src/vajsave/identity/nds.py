"""NDS identity resolver: match the save name against ``.nds`` ROMs."""

from __future__ import annotations

from .models import GameIdentityResult
from .roms import resolve_rom_identity

PLATFORM = "nds"
EXTENSIONS = (".nds",)


def resolve(entry, ctx) -> GameIdentityResult:
    return resolve_rom_identity(entry, ctx, platform=PLATFORM)
