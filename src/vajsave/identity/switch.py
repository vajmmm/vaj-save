"""Nintendo Switch identity resolver.

Reuses the title id parsed from Checkpoint folder names (``0100...``) or falls
back to the JKSV display name as a partial identity.
"""

from __future__ import annotations

import re

from ..artwork.switch_covers import get_switch_id_for_title, get_switch_title_for_id
from ..platforms.switch import _clean_switch_title_id, _parse_checkpoint_folder_name
from .models import SOURCE_METADATA
from .titled import resolve_from_title_id

PLATFORM = "switch"


def resolve(entry, ctx):
    title_id = getattr(entry, "title_id", None)
    display = (getattr(entry, "display_name", None) or "").strip()

    if not title_id and display:
        parsed_tid, parsed_display = _parse_checkpoint_folder_name(display)
        if parsed_tid:
            title_id = parsed_tid
            if parsed_display and parsed_display != display:
                display = parsed_display

    if not title_id and display:
        matched_tid = get_switch_id_for_title(display)
        if matched_tid:
            title_id = matched_tid
            canonical = get_switch_title_for_id(matched_tid)
            if canonical:
                display = canonical

    if title_id:
        # If display name is still a hex Title ID, resolve it to canonical game title
        if _clean_switch_title_id(display) or not display or (display.startswith("0x") and len(display.split()) <= 2):
            known_name = get_switch_title_for_id(title_id)
            if known_name:
                display = known_name

    return resolve_from_title_id(
        entry,
        platform=PLATFORM,
        title_id=title_id,
        title=display,
        source=SOURCE_METADATA,
        missing_reason="缺少 Switch title_id",
    )
