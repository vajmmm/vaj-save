"""PS Vita identity resolver.

Native savedata carries ``sce_sys/param.sfo``; exported savegames only carry the
directory name.  Both paths reuse the title id captured by the scanner, enriched
by the SFO when it is present.
"""

from __future__ import annotations

from pathlib import Path

from ..sfo import parse_sfo
from .models import SOURCE_METADATA, SOURCE_SFO
from .titled import resolve_from_title_id

PLATFORM = "vita"


def _read_param_sfo(directory):
    try:
        path = Path(directory) / "sce_sys" / "param.sfo"
        if path.is_file():
            return parse_sfo(path)
    except OSError:
        return {}
    return {}


def resolve(entry, ctx):
    sfo_data = _read_param_sfo(entry.path)
    return resolve_from_title_id(
        entry,
        platform=PLATFORM,
        title_id=sfo_data.get("TITLE_ID"),
        title=sfo_data.get("TITLE"),
        source=SOURCE_SFO if sfo_data else SOURCE_METADATA,
        missing_reason="缺少 Vita title_id / PARAM.SFO",
    )
