"""Independent ``GameIdentity`` layer.

Public surface:

* :class:`GameIdentity` / :class:`GameIdentityResult` value objects
* :class:`GameIdentityResolver` platform dispatcher (``resolve`` / ``resolve_many``)
* :class:`BindingStore` path-independent manual/automatic bindings
* :class:`RomIndex` / :class:`RomFile` ROM discovery and matching
"""

from .bindings import BINDINGS_NAME, BindingStore
from .models import (
    SOURCE_BINDING,
    SOURCE_FILENAME,
    SOURCE_MANUAL,
    SOURCE_METADATA,
    SOURCE_ROM,
    SOURCE_SFO,
    STATUS_AMBIGUOUS,
    STATUS_PARTIAL,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    STATUSES,
    GameIdentity,
    GameIdentityResult,
    ambiguous,
    partial,
    resolved,
    unresolved,
)
from .naming import (
    display_name_from_stem,
    extract_region,
    normalize_title,
    save_hint,
    strip_extension,
)
from .resolver import GameIdentityResolver, ResolverContext
from .roms import RomFile, RomIndex, build_identity_from_rom, make_rom_file
from .digest import crc32_file, digest_file, sha1_file

__all__ = [
    "GameIdentity",
    "GameIdentityResult",
    "GameIdentityResolver",
    "ResolverContext",
    "BindingStore",
    "BINDINGS_NAME",
    "RomIndex",
    "RomFile",
    "build_identity_from_rom",
    "make_rom_file",
    "digest_file",
    "sha1_file",
    "crc32_file",
    "normalize_title",
    "extract_region",
    "display_name_from_stem",
    "strip_extension",
    "save_hint",
    "resolved",
    "partial",
    "ambiguous",
    "unresolved",
    "STATUSES",
    "STATUS_RESOLVED",
    "STATUS_PARTIAL",
    "STATUS_AMBIGUOUS",
    "STATUS_UNRESOLVED",
    "SOURCE_ROM",
    "SOURCE_BINDING",
    "SOURCE_MANUAL",
    "SOURCE_METADATA",
    "SOURCE_SFO",
    "SOURCE_FILENAME",
]
