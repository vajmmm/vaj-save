"""Platform-dispatching game identity resolver.

``GameIdentityResolver`` owns the shared :class:`~.roms.RomIndex` and
:class:`~.bindings.BindingStore`, then delegates each save to a per-platform
resolver module.  Unknown platforms resolve to ``unresolved`` rather than
raising, and ``resolve_many`` isolates per-save failures.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Union

from . import gba, nds, psp, switch, threeds, vita
from .bindings import BindingStore
from .models import GameIdentity, GameIdentityResult, unresolved
from .roms import RomIndex, build_identity_from_rom, make_rom_file

_PLATFORM_MODULES = {
    "gba": gba,
    "nds": nds,
    "psp": psp,
    "vita": vita,
    "3ds": threeds,
    "switch": switch,
}


@dataclass
class ResolverContext:
    """Shared dependencies handed to every per-platform resolver."""

    bindings: BindingStore
    rom_index: RomIndex
    auto_bind: bool = True


class GameIdentityResolver:
    def __init__(
        self,
        *,
        rom_dirs: Optional[Mapping[str, Iterable]] = None,
        bindings: Optional[BindingStore] = None,
        binding_path: Optional[Union[Path, str]] = None,
        auto_bind: bool = True,
    ) -> None:
        if bindings is not None:
            self.bindings = bindings
        else:
            self.bindings = BindingStore(binding_path)
        self.rom_index = RomIndex(rom_dirs)
        self.auto_bind = bool(auto_bind)

    # -- dispatch ------------------------------------------------------------

    @staticmethod
    def module_for(platform: Optional[str]):
        return _PLATFORM_MODULES.get((platform or "").strip().lower())

    def _context(self) -> ResolverContext:
        return ResolverContext(
            bindings=self.bindings,
            rom_index=self.rom_index,
            auto_bind=self.auto_bind,
        )

    def resolve(self, entry) -> GameIdentityResult:
        module = self.module_for(getattr(entry, "platform", None))
        if module is None:
            return unresolved(
                reason=f"暂不支持的身份解析: {getattr(entry, 'platform', None) or 'unknown'}",
                save_path=getattr(entry, "path", None),
            )
        return module.resolve(entry, self._context())

    def resolve_many(self, entries: Iterable) -> List[GameIdentityResult]:
        results: List[GameIdentityResult] = []
        for entry in entries:
            try:
                results.append(self.resolve(entry))
            except Exception as exc:  # noqa: BLE001 - one bad save must not stop the batch
                results.append(
                    unresolved(
                        reason=f"身份解析失败: {exc}",
                        save_path=getattr(entry, "path", None),
                    )
                )
        return results

    # -- maintenance ---------------------------------------------------------

    def refresh(self) -> None:
        self.rom_index.refresh()

    def bind(
        self,
        entry,
        identity: Optional[GameIdentity] = None,
        *,
        rom_path: Optional[Union[Path, str]] = None,
    ) -> GameIdentity:
        """Persist a manual binding for ``entry``.

        Pass either a ready :class:`GameIdentity` or a ``rom_path`` to derive one
        from a ROM file (hashed + header parsed like an automatic match).
        """
        if identity is None and rom_path is not None:
            platform = (getattr(entry, "platform", "") or "").strip().lower()
            identity = build_identity_from_rom(make_rom_file(rom_path, platform), platform)
            if identity is None:
                raise ValueError(f"无法读取 ROM: {rom_path}")
        if identity is None:
            raise ValueError("bind 需要 identity 或 rom_path")
        return self.bindings.set(entry, identity, manual=True)


__all__ = ["GameIdentityResolver", "ResolverContext"]
