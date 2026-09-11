"""Nintendo 3DS identity resolver.

Reuses the title id extracted from Checkpoint/JKSM folder names.  3DS SD cards
whose saves are encrypted never reach the resolver (they produce no save rows).
"""

from __future__ import annotations

from .models import SOURCE_METADATA
from .titled import resolve_from_title_id

PLATFORM = "3ds"


def resolve(entry, ctx):
    return resolve_from_title_id(
        entry,
        platform=PLATFORM,
        source=SOURCE_METADATA,
        missing_reason="缺少 3DS title_id",
    )
