"""Independent ``GameMetadata`` layer.

Public surface:

* :class:`GameMetadata` -- a frozen canonical description of a ROM, strictly
  separate from :class:`~vajsave.identity.GameIdentity`;
* :class:`LibretroIndex` -- compact-JSON + Logiqx-XML parser with an in-memory
  SHA-1/CRC32 lookup;
* :class:`MetadataCache` -- persistent, thread-safe JSON cache keyed by
  ``identity_key`` (``game_metadata.json``);
* :class:`MetadataProvider` / :class:`LibretroMetadataProvider` -- the provider
  interface and its libretro implementation, ``resolve(identity)``;
* :class:`GameMetadataResolver` -- cache-first resolver, the application path.
"""

from .cache import METADATA_CACHE_NAME, MetadataCache
from .libretro import (
    INDEX_FORMAT,
    INDEX_FORMAT_VERSION,
    SUPPORTED_PLATFORMS,
    LibretroIndex,
    detect_platform,
    external_ids_for,
)
from .models import SOURCE_LIBRETRO, GameMetadata
from .paths import bundled_libretro_dir, default_libretro_dirs
from .service import GameMetadataResolver, LibretroMetadataProvider, MetadataProvider

__all__ = [
    "GameMetadata",
    "SOURCE_LIBRETRO",
    "LibretroIndex",
    "SUPPORTED_PLATFORMS",
    "INDEX_FORMAT",
    "INDEX_FORMAT_VERSION",
    "detect_platform",
    "external_ids_for",
    "MetadataCache",
    "METADATA_CACHE_NAME",
    "MetadataProvider",
    "LibretroMetadataProvider",
    "GameMetadataResolver",
    "bundled_libretro_dir",
    "default_libretro_dirs",
]
