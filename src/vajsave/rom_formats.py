"""Canonical per-platform ROM/container extensions.

A single definition shared by the save scanners (:mod:`vajsave.platforms`) and
the identity layer (:mod:`vajsave.identity`).  Keeping it in one place means a
newly confirmed dump extension is discovered *and* matched identically
everywhere, instead of drifting apart between the scanner and the ROM index.
"""

from __future__ import annotations

from typing import Dict, Tuple

# Cartridge ROM extensions per platform.  GBA dumps occasionally use ``.agb``;
# some Wood R4 cards ship NDS dumps as ``.ids`` (confirmed on a real card - the
# files carry a normal NDS header and pair with a same-stem ``.sav``).
ROM_EXTENSIONS: Dict[str, Tuple[str, ...]] = {
    "gba": (".gba", ".agb"),
    "nds": (".nds", ".ids"),
    "gb": (".gb",),
    "gbc": (".gbc",),
}


def supported_extensions(platform: str) -> Tuple[str, ...]:
    """Extensions accepted as ROM dumps for ``platform``; empty when unconstrained."""
    return ROM_EXTENSIONS.get((platform or "").strip().lower(), ())


__all__ = ["ROM_EXTENSIONS", "supported_extensions"]
