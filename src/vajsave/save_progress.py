"""Short progress text already stored in a save container.

PSP and Vita write the line shown in the console's own save menu into
``PARAM.SFO`` (chapter, play time, character). Cartridge and Switch saves do
not share a field like that, so this module leaves them blank instead of
guessing a percentage.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Order matches what the console shows under the save title.
_SFO_PROGRESS_KEYS = ("SUB_TITLE", "DETAIL", "SAVEDATA_DETAIL", "SAVEDATA_TITLE")
_MAX_LEN = 80


def _clean(value: object) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) > _MAX_LEN:
        return text[: _MAX_LEN - 1].rstrip() + "…"
    return text


def progress_from_sfo(sfo: Optional[Dict[str, Any]]) -> str:
    """Return a progress line from an SFO, or ``""`` when it only repeats the title."""
    if not sfo:
        return ""
    title = _clean(sfo.get("TITLE")).casefold()
    title_id = _clean(sfo.get("TITLE_ID")).casefold()
    for key in _SFO_PROGRESS_KEYS:
        text = _clean(sfo.get(key))
        if not text:
            continue
        folded = text.casefold()
        if folded == title or folded == title_id:
            continue
        return text
    return ""


def progress_extra(sfo: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """``SaveEntry.extra`` fragment, or ``None`` when there is nothing to store."""
    text = progress_from_sfo(sfo)
    if not text:
        return None
    return {"progress": text}


def progress_text(entry: object) -> str:
    """Progress line previously stored on a save entry."""
    extra = getattr(entry, "extra", None) or {}
    if not isinstance(extra, dict):
        return ""
    return _clean(extra.get("progress"))
