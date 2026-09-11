"""Independent artwork layer: providers, downloader, cache and resolution.

Public surface:

* :class:`ArtworkProvider` / :class:`LibretroArtworkProvider` -- filename + URL
  rules (official libretro thumbnail layout);
* :class:`ArtworkDownloader` -- bounded, never-raising HTTP GET;
* :class:`CoverCache` -- identity-hash named files plus a JSON manifest;
* :class:`ArtworkService` / :func:`resolve_artwork` -- the
  ``user > downloaded > embedded > placeholder`` fallback order.
"""

from .cache import (
    COVER_CACHE_DIR,
    MANIFEST_NAME,
    CoverCache,
    is_valid_image_bytes,
)
from .downloader import DEFAULT_TIMEOUT, MAX_DOWNLOAD_BYTES, ArtworkDownloader
from .loader import ArtworkLoader
from .providers import (
    LIBRETRO_SYSTEM_NAMES,
    LIBRETRO_THUMBNAIL_BASE,
    ArtworkProvider,
    ArtworkRef,
    LibretroArtworkProvider,
    sanitize_libretro_filename,
)
from .service import (
    PLACEHOLDER,
    SOURCE_DOWNLOADED,
    SOURCE_EMBEDDED,
    SOURCE_PLACEHOLDER,
    SOURCE_USER,
    ArtworkResolution,
    ArtworkService,
    resolve_artwork,
)

__all__ = [
    "ArtworkProvider",
    "ArtworkRef",
    "LibretroArtworkProvider",
    "LIBRETRO_THUMBNAIL_BASE",
    "LIBRETRO_SYSTEM_NAMES",
    "sanitize_libretro_filename",
    "ArtworkDownloader",
    "DEFAULT_TIMEOUT",
    "MAX_DOWNLOAD_BYTES",
    "ArtworkLoader",
    "CoverCache",
    "COVER_CACHE_DIR",
    "MANIFEST_NAME",
    "is_valid_image_bytes",
    "ArtworkService",
    "ArtworkResolution",
    "resolve_artwork",
    "PLACEHOLDER",
    "SOURCE_USER",
    "SOURCE_DOWNLOADED",
    "SOURCE_EMBEDDED",
    "SOURCE_PLACEHOLDER",
]
