"""Metadata provider + resolver: cache-first, provider-backed, never raising.

The layering is:

* :class:`MetadataProvider` -- the interface, ``resolve(identity)``;
* :class:`LibretroMetadataProvider` -- the local libretro/No-Intro index, loaded
  lazily and exactly once;
* :class:`GameMetadataResolver` -- the application entry point.  A **cache hit
  returns immediately without consulting the provider** ("cache hit -> zero
  provider"); a cache miss asks the provider and stores a positive result.

Any failure at either layer degrades to ``None``: metadata is a nice-to-have and
must never break scanning, identity, backup or the UI.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Iterable, List, Optional, Union

from .cache import MetadataCache
from .libretro import SUPPORTED_PLATFORMS, LibretroIndex
from .models import GameMetadata
from .paths import default_libretro_dirs

__all__ = [
    "MetadataProvider",
    "LibretroMetadataProvider",
    "GameMetadataResolver",
]


def _platform_of(identity) -> str:
    return str(getattr(identity, "platform", "") or "").strip().lower()


def _digests(identity):
    return (
        getattr(identity, "rom_sha1", None),
        getattr(identity, "rom_crc32", None),
    )


class MetadataProvider:
    """Interface for a metadata source, keyed by a game identity."""

    def resolve(self, identity) -> Optional[GameMetadata]:  # pragma: no cover - interface
        raise NotImplementedError


class LibretroMetadataProvider(MetadataProvider):
    """A local libretro/No-Intro index, loaded once on first use."""

    def __init__(
        self,
        dirs: Optional[Iterable[Union[Path, str]]] = None,
        *,
        index: Optional[LibretroIndex] = None,
    ) -> None:
        # ``None`` means "the default search path": the app config dir, the
        # library drop-in dir and the bundled compact index.  An explicit (even
        # empty) iterable is honoured as-is.
        resolved_dirs = default_libretro_dirs() if dirs is None else dirs
        self._dirs: List[Path] = [Path(d).expanduser() for d in resolved_dirs]
        self._index = index
        self._loaded = index is not None

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def dirs(self) -> List[Path]:
        return list(self._dirs)

    def _ensure_index(self) -> LibretroIndex:
        if self._index is None:
            self._index = LibretroIndex.from_paths(self._dirs)
        self._loaded = True
        return self._index

    def resolve(self, identity) -> Optional[GameMetadata]:
        if identity is None:
            return None
        platform = _platform_of(identity)
        if platform not in SUPPORTED_PLATFORMS:
            return None
        sha1, crc32 = _digests(identity)
        if not sha1 and not crc32:
            return None
        try:
            found = self._ensure_index().lookup(platform=platform, sha1=sha1, crc32=crc32)
        except Exception:  # noqa: BLE001 - metadata must never break the caller
            return None
        if found is None:
            return None
        return dataclasses.replace(found, identity_key=getattr(identity, "identity_key", "") or found.identity_key)


class GameMetadataResolver:
    """Cache-first metadata resolver with a pluggable provider."""

    def __init__(
        self,
        provider: Optional[MetadataProvider] = None,
        cache: Optional[MetadataCache] = None,
    ) -> None:
        self.provider = provider
        self.cache = cache if cache is not None else MetadataCache()

    def resolve(self, identity) -> Optional[GameMetadata]:
        """Metadata for ``identity``; cache first, provider on a miss."""
        if identity is None:
            return None
        cached = self.cached(identity)
        if cached is not None:
            return cached
        if self.provider is None:
            return None
        try:
            found = self.provider.resolve(identity)
        except Exception:  # noqa: BLE001 - a broken provider is just "no metadata"
            return None
        if found is not None:
            try:
                self.cache.put(found)
            except Exception:  # noqa: BLE001
                pass
        return found

    def cached(self, identity) -> Optional[GameMetadata]:
        """Cache-only lookup: never touches the provider (UI hot path)."""
        if identity is None:
            return None
        key = getattr(identity, "identity_key", None)
        if not key:
            return None
        try:
            return self.cache.get(key)
        except Exception:  # noqa: BLE001
            return None
