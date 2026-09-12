"""Independent artwork layer: providers, downloader, cache and resolution.

Public surface:

* :class:`Artwork` / :class:`ArtworkProvider` /
  :class:`LibretroThumbnailProvider` -- the ``find_cover(metadata)`` contract and
  the official libretro thumbnail filename + URL rules;
* :class:`ArtworkDownloader` -- bounded, never-raising HTTP GET;
* :class:`CoverCache` -- ``covers/<platform>/<identity-hash>.png`` files plus a
  JSON manifest with the required provenance fields;
* :class:`ArtworkService` / :func:`resolve_artwork` -- the
  ``user > downloaded > embedded > placeholder`` fallback order;
* :class:`ArtworkLoader` -- background execution marshalled back via
  ``schedule`` (``root.after``).
"""

from .cache import (
    COVER_CACHE_DIR,
    MANIFEST_FIELDS,
    MANIFEST_NAME,
    MAX_COVER_BYTES,
    CoverCache,
    is_valid_image_bytes,
)
from .downloader import DEFAULT_TIMEOUT, MAX_DOWNLOAD_BYTES, ArtworkDownloader
from .loader import ArtworkLoader
from .llm_choice import (
    ANTHROPIC_API_VERSION,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_LLM_PROTOCOL,
    DEFAULT_LLM_TIMEOUT,
    LLM_LOG_NAME,
    LLM_PROTOCOLS,
    PROTOCOL_ANTHROPIC,
    PROTOCOL_OPENAI,
    LLMCoverChooser,
    choose_cover_filename,
    default_base_url,
    default_model,
    normalize_base_url,
    normalize_protocol,
)
from .providers import (
    LIBRETRO_SYSTEM_NAMES,
    LIBRETRO_THUMBNAIL_BASE,
    Artwork,
    ArtworkProvider,
    LibretroThumbnailProvider,
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

# Historical aliases (pre-rename) kept so older imports keep working.
LibretroArtworkProvider = LibretroThumbnailProvider
ArtworkRef = Artwork

__all__ = [
    "Artwork",
    "ArtworkProvider",
    "LibretroThumbnailProvider",
    "LibretroArtworkProvider",
    "ArtworkRef",
    "LIBRETRO_THUMBNAIL_BASE",
    "LIBRETRO_SYSTEM_NAMES",
    "sanitize_libretro_filename",
    "ArtworkDownloader",
    "DEFAULT_TIMEOUT",
    "MAX_DOWNLOAD_BYTES",
    "ArtworkLoader",
    "LLMCoverChooser",
    "choose_cover_filename",
    "ANTHROPIC_API_VERSION",
    "DEFAULT_ANTHROPIC_BASE_URL",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_LLM_BASE_URL",
    "DEFAULT_LLM_MODEL",
    "DEFAULT_LLM_PROTOCOL",
    "DEFAULT_LLM_TIMEOUT",
    "LLM_LOG_NAME",
    "LLM_PROTOCOLS",
    "PROTOCOL_ANTHROPIC",
    "PROTOCOL_OPENAI",
    "default_base_url",
    "default_model",
    "normalize_base_url",
    "normalize_protocol",
    "CoverCache",
    "COVER_CACHE_DIR",
    "MANIFEST_NAME",
    "MANIFEST_FIELDS",
    "MAX_COVER_BYTES",
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
