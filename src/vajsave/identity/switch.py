"""Nintendo Switch identity resolver.

Reuses the title id parsed from Checkpoint folder names (``0100...``) or falls
back to the JKSV display name as a partial identity.
"""

from __future__ import annotations

from .models import SOURCE_METADATA
from .titled import resolve_from_title_id

PLATFORM = "switch"


def resolve(entry, ctx):
    return resolve_from_title_id(
        entry,
        platform=PLATFORM,
        source=SOURCE_METADATA,
        missing_reason="缺少 Switch title_id",
    )
