"""Metadata lookup service: cache-first, provider-backed, never raising.

The service is the single entry point the rest of the app (and the UI) uses:

* a **cache hit returns immediately without consulting the provider** (the
  acceptance requirement "cache hit -> zero provider");
* a cache miss asks the :class:`MetadataProvider` (the local libretro index) and
  stores a positive result;
* any failure at either layer degrades to ``None`` -- metadata is a nice-to-have
  and must never break scanning, identity or the UI.

``LibretroMetadataProvider`` loads its index lazily and exactly once: the first
lookup parses the configured ``.dat`` files and builds the in-memory digest maps,
and every later lookup is a plain dict access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Union

from .cache import MetadataCache
from .libretro import LibretroIndex
from .models import GameMetadata


class MetadataProvider:
    """Interface for a metadata source (implemented by the libretro index)."""

    def lookup(
        self,
        *,
        platform: str,
        sha1: Optional[str] = None,
        crc32: Optional[str] = None,
    ) -> Optional[GameMetadata]:  # pragma: no cover - interface
        raise NotImplementedError


class LibretroMetadataProvider(MetadataProvider):
    """A local libretro/No-Intro index, loaded once on first use."""

    def __init__(
        self,
        dirs: Optional[Iterable[Union[Path, str]]] = None,
        *,
        index: Optional[LibretroIndex] = None,
    ) -> None:
        self._dirs: List[Path] = [Path(d).expanduser() for d in (dirs or [])]
        self._index = index
        self._loaded = index is not None

    @property
    def loaded(self) -> bool:
        return self._loaded

    def _ensure_index(self) -> LibretroIndex:
        if self._index is None:
            self._index = LibretroIndex.from_paths(self._dirs)
        self._loaded = True
        return self._index

    def lookup(
        self,
        *,
        platform: str,
        sha1: Optional[str] = None,
        crc32: Optional[str] = None,
    ) -> Optional[GameMetadata]:
        try:
            return self._ensure_index().lookup(platform=platform, sha1=sha1, crc32=crc32)
        except Exception:  # noqa: BLE001 - metadata must never break the caller
            return None


class MetadataService:
    """Cache-first metadata resolver with a pluggable provider."""

    def __init__(
        self,
        provider: Optional[MetadataProvider] = None,
        cache: Optional[MetadataCache] = None,
    ) -> None:
        self.provider = provider
        self.cache = cache if cache is not None else MetadataCache()

    def lookup(
        self,
        platform: str,
        *,
        sha1: Optional[str] = None,
        crc32: Optional[str] = None,
    ) -> Optional[GameMetadata]:
        if not platform or (not sha1 and not crc32):
            return None
        cached = self.cached(platform, sha1=sha1, crc32=crc32)
        if cached is not None:
            return cached
        if self.provider is None:
            return None
        try:
            found = self.provider.lookup(platform=platform, sha1=sha1, crc32=crc32)
        except Exception:  # noqa: BLE001 - a broken provider is just "no metadata"
            return None
        if found is not None:
            try:
                self.cache.put(found)
            except Exception:  # noqa: BLE001
                pass
        return found

    def cached(
        self,
        platform: str,
        *,
        sha1: Optional[str] = None,
        crc32: Optional[str] = None,
    ) -> Optional[GameMetadata]:
        """Cache-only lookup: never touches the provider (UI hot path)."""
        if not platform or (not sha1 and not crc32):
            return None
        try:
            return self.cache.get(platform, sha1=sha1, crc32=crc32)
        except Exception:  # noqa: BLE001
            return None

    def for_identity(self, identity) -> Optional[GameMetadata]:
        """Convenience wrapper around a :class:`GameIdentity`-like object."""
        if identity is None:
            return None
        return self.lookup(
            getattr(identity, "platform", "") or "",
            sha1=getattr(identity, "rom_sha1", None),
            crc32=getattr(identity, "rom_crc32", None),
        )


__all__ = ["MetadataProvider", "LibretroMetadataProvider", "MetadataService"]
