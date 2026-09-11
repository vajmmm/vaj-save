"""Shared resolver for platforms whose identity is a scanned title id.

Vita, 3DS and Switch saves do not have a ROM to hash.  Their identity is the
platform's own title id captured during the scan; when that is missing only the
display name is known, which is a ``partial`` identity rather than ``resolved``.
"""

from __future__ import annotations

from .models import (
    SOURCE_FILENAME,
    SOURCE_METADATA,
    GameIdentity,
    GameIdentityResult,
    partial,
    resolved,
    unresolved,
)
from .naming import normalize_title


def resolve_from_title_id(
    entry,
    *,
    platform: str,
    title_id=None,
    title=None,
    source: str = SOURCE_METADATA,
    missing_reason: str = "缺少 title_id",
) -> GameIdentityResult:
    resolved_id = (title_id or getattr(entry, "title_id", None) or "").strip()
    display = (title or getattr(entry, "display_name", None) or "").strip()

    if resolved_id:
        identity = GameIdentity(
            identity_key=f"{platform}:{resolved_id}",
            platform=platform,
            title=display or resolved_id,
            title_id=resolved_id,
            source=source,
        )
        return resolved(identity, save_path=entry.path)

    if display:
        identity = GameIdentity(
            identity_key=f"{platform}:name:{normalize_title(display)}",
            platform=platform,
            title=display,
            source=SOURCE_FILENAME,
        )
        return partial(identity, reason=missing_reason, save_path=entry.path)

    return unresolved(reason=missing_reason, save_path=entry.path)
