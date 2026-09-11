"""Independent ``GameMetadata`` layer.

Public surface:

* :class:`GameMetadata` -- a frozen canonical description of a ROM, strictly
  separate from :class:`~vajsave.identity.GameIdentity`;
* :class:`LibretroIndex` -- one-shot parser for local libretro/No-Intro ``.dat``
  files plus an in-memory SHA-1/CRC32 lookup;
* :class:`MetadataCache` -- persistent JSON cache keyed by ROM digest;
* :class:`MetadataService` / :class:`LibretroMetadataProvider` -- cache-first
  lookup with a lazily loaded provider.
"""

from .cache import METADATA_CACHE_NAME, MetadataCache, metadata_key
from .libretro import SUPPORTED_PLATFORMS, LibretroIndex, detect_platform
from .models import SOURCE_LIBRETRO, GameMetadata
from .paths import bundled_libretro_dir, default_libretro_dirs
from .service import LibretroMetadataProvider, MetadataProvider, MetadataService

__all__ = [
    "GameMetadata",
    "SOURCE_LIBRETRO",
    "LibretroIndex",
    "SUPPORTED_PLATFORMS",
    "detect_platform",
    "MetadataCache",
    "METADATA_CACHE_NAME",
    "metadata_key",
    "MetadataProvider",
    "LibretroMetadataProvider",
    "MetadataService",
    "bundled_libretro_dir",
    "default_libretro_dirs",
]
