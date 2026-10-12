"""Identity for PS3, PS4, Wii U, Wii and Xbox 360.

These saves have no ROM to hash. The scanned product code or title id is the
identity, the same way Vita and Switch already work. PARAM.SFO is re-read when
the folder is still on disk so a directory name like ``BLUS30123-AUTO`` does
not replace the SFO title.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..platforms.console_ids import (
    normalize_ps3_title_id,
    normalize_ps4_title_id,
    normalize_wii_title_id,
    normalize_wiiu_title_id,
    normalize_x360_title_id,
)
from ..sfo import parse_sfo
from .models import SOURCE_METADATA, SOURCE_SFO
from .titled import resolve_from_title_id

def _read_param_sfo(directory, *, sce_sys: bool) -> dict:
    try:
        base = Path(directory)
    except TypeError:
        return {}
    candidates = []
    if sce_sys:
        candidates.append(base / "sce_sys" / "param.sfo")
    candidates.extend((base / "PARAM.SFO", base / "param.sfo"))
    for path in candidates:
        try:
            if path.is_file():
                data = parse_sfo(path)
                if data:
                    return data
        except OSError:
            continue
    return {}


def _finish(entry, platform: str, title_id: Optional[str], title: Optional[str], source: str):
    return resolve_from_title_id(
        entry,
        platform=platform,
        title_id=title_id,
        title=title,
        source=source,
        missing_reason=f"缺少 {platform} title id",
    )


def _resolve_playstation(entry, platform: str, clean):
    scanned = clean(getattr(entry, "title_id", None))
    display = str(getattr(entry, "display_name", None) or "").strip()
    sfo = _read_param_sfo(getattr(entry, "path", None), sce_sys=(platform == "ps4"))
    sfo_id = clean(sfo.get("TITLE_ID")) or clean(sfo.get("SAVEDATA_DIRECTORY"))
    title_id = sfo_id or scanned
    title = str(sfo.get("TITLE") or "").strip() or display or title_id
    return _finish(
        entry,
        platform,
        title_id,
        title,
        SOURCE_SFO if sfo else SOURCE_METADATA,
    )


def _resolve_code(entry, platform: str, clean):
    scanned = clean(getattr(entry, "title_id", None)) or clean(
        getattr(entry, "display_name", None)
    )
    display = str(getattr(entry, "display_name", None) or "").strip()
    title = display or scanned
    if scanned and display and clean(display) == scanned:
        title = scanned
    return _finish(entry, platform, scanned, title, SOURCE_METADATA)


def resolve(entry, ctx):
    del ctx  # home consoles do not consult the ROM index
    platform = str(getattr(entry, "platform", None) or "").strip().lower()
    if platform == "ps3":
        return _resolve_playstation(entry, "ps3", normalize_ps3_title_id)
    if platform == "ps4":
        return _resolve_playstation(entry, "ps4", normalize_ps4_title_id)
    if platform == "wii":
        return _resolve_code(entry, "wii", normalize_wii_title_id)
    if platform == "wiiu":
        return _resolve_code(entry, "wiiu", normalize_wiiu_title_id)
    if platform == "x360":
        return _resolve_code(entry, "x360", normalize_x360_title_id)
    return _finish(entry, platform or "unknown", None, None, SOURCE_METADATA)


__all__ = ["resolve"]
