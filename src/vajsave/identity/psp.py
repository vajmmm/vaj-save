"""PSP identity resolver: read ``PARAM.SFO`` for ``TITLE_ID``.

SFO parsing never raises (see :func:`vajsave.sfo.parse_sfo`); a missing or
corrupt file simply degrades to the metadata the scanner already captured.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..sfo import parse_sfo
from .models import (
    SOURCE_FILENAME,
    SOURCE_METADATA,
    SOURCE_SFO,
    GameIdentity,
    GameIdentityResult,
    partial,
    resolved,
    unresolved,
)
from .naming import normalize_title

PLATFORM = "psp"


def _find_param_sfo(directory) -> Optional[Path]:
    try:
        base = Path(directory)
        if not base.is_dir():
            return None
        for child in sorted(base.iterdir()):
            try:
                if child.name.lower() == "param.sfo" and child.is_file():
                    return child
            except OSError:
                continue
    except OSError:
        return None
    return None


def resolve(entry, ctx) -> GameIdentityResult:
    entry_title_id = str(getattr(entry, "title_id", None) or "").strip()
    display = str(getattr(entry, "display_name", None) or "").strip()
    if entry_title_id and display and display != entry_title_id:
        identity = GameIdentity(
            identity_key=f"psp:{entry_title_id}",
            platform=PLATFORM,
            title=display,
            title_id=entry_title_id,
            source=SOURCE_METADATA,
        )
        return resolved(identity, save_path=entry.path)

    sfo_path = _find_param_sfo(entry.path)
    sfo_data = parse_sfo(sfo_path) if sfo_path is not None else {}
    title_id = (
        sfo_data.get("TITLE_ID")
        or sfo_data.get("SAVEDATA_DIRECTORY")
        or entry.title_id
    )
    title = sfo_data.get("TITLE") or entry.display_name or title_id
    source = SOURCE_SFO if sfo_data else SOURCE_METADATA

    if title_id:
        identity = GameIdentity(
            identity_key=f"psp:{title_id}",
            platform=PLATFORM,
            title=title or title_id,
            title_id=title_id,
            source=source,
        )
        return resolved(identity, save_path=entry.path)

    fallback_title = entry.display_name or sfo_data.get("TITLE")
    if fallback_title:
        identity = GameIdentity(
            identity_key=f"psp:name:{normalize_title(fallback_title)}",
            platform=PLATFORM,
            title=fallback_title,
            source=SOURCE_FILENAME,
        )
        return partial(identity, reason="缺少 PARAM.SFO TITLE_ID", save_path=entry.path)
    return unresolved(reason="缺少 PARAM.SFO / TITLE_ID", save_path=entry.path)
