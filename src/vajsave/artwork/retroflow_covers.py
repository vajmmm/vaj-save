"""PS Vita and PSP physical retail box art provider using Title IDs.

Fetches authentic physical retail box artwork from the community-maintained
RetroFlow / HexFlow repository (jimbob4000/hexflow-covers):
- PS Vita: 250x320 retail blue-case box art matching authentic Keep Case aspect
- PSP: 180x320 retail UMD plastic case box art

Indexed directly by PlayStation Title ID (e.g. PCSD00071, NPJH50107), bypassing
any regional title translation or naming discrepancy.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from .providers import Artwork, ArtworkProvider

logger = logging.getLogger("vajsave.artwork.retroflow")

_PSN_TITLE_ID_RE = re.compile(r"([A-Za-z]{4}\d{5})")
_ALNUM_RE = re.compile(r"^[A-Za-z0-9_-]{4,12}$")

RETROFLOW_SYSTEMS = {
    "vita": "PSVita",
    "ps vita": "PSVita",
    "psp": "PSP",
}

# Fast multi-CDN endpoints with global caching
CDN_TEMPLATES = [
    "https://fastly.jsdelivr.net/gh/jimbob4000/hexflow-covers@main/Covers/{system}/{title_id}.png",
    "https://cdn.jsdelivr.net/gh/jimbob4000/hexflow-covers@main/Covers/{system}/{title_id}.png",
    "https://raw.githubusercontent.com/jimbob4000/hexflow-covers/main/Covers/{system}/{title_id}.png",
]


def clean_playstation_title_id(raw_id: Any) -> Optional[str]:
    """Extract canonical PlayStation Product Code / Title ID (e.g. PCSD00071, NPJH50107)."""
    text = str(raw_id or "").strip()
    if not text:
        return None
    match = _PSN_TITLE_ID_RE.search(text)
    if match:
        return match.group(1).upper()
    cleaned = text.replace(" ", "").upper()
    if cleaned in ("VITASHELL", "SKGD3PL0Y", "AUTOPLUG2", "MOLECULAR", "PKGI00000", "ENSO00000"):
        return cleaned
    return None


class RetroFlowCoverProvider(ArtworkProvider):
    """Artwork provider for PS Vita and PSP physical retail box art."""

    name = "retroflow"

    def supports(self, platform: str) -> bool:
        return (platform or "").strip().lower() in RETROFLOW_SYSTEMS

    def cover_candidates_for_title_id(
        self, platform: str, raw_title_id: str
    ) -> List[Artwork]:
        """Generate candidate artworks for a given PlayStation Title ID."""
        plat = (platform or "").strip().lower()
        system = RETROFLOW_SYSTEMS.get(plat)
        if not system:
            return []

        title_id = clean_playstation_title_id(raw_title_id)
        if not title_id:
            return []

        candidates: List[Artwork] = []
        for tpl in CDN_TEMPLATES:
            url = tpl.format(system=system, title_id=title_id)
            candidates.append(
                Artwork(
                    url=url,
                    filename=f"{title_id}.png",
                    provider=self.name,
                    platform=plat,
                    canonical_title=title_id,
                )
            )
        return candidates

    def cover_for(self, platform: str, title: str) -> Optional[Artwork]:
        """Artwork lookup via title (resolves if title is or contains a Title ID)."""
        plat = (platform or "").strip().lower()
        if not self.supports(plat):
            return None
        title_id = clean_playstation_title_id(title)
        if not title_id:
            return None
        candidates = self.cover_candidates_for_title_id(plat, title_id)
        return candidates[0] if candidates else None

    def find_cover(self, metadata: Any) -> Optional[Artwork]:
        """Artwork lookup via metadata object."""
        if metadata is None:
            return None
        if isinstance(metadata, dict):
            platform = metadata.get("platform")
            raw_id = metadata.get("title_id") or metadata.get("save_id") or metadata.get("title")
        else:
            platform = getattr(metadata, "platform", None)
            raw_id = (
                getattr(metadata, "title_id", None)
                or getattr(metadata, "save_id", None)
                or getattr(metadata, "title", None)
            )
        if not self.supports(platform):
            return None
        candidates = self.cover_candidates_for_title_id(platform, raw_id)
        return candidates[0] if candidates else None

    def boxart_listing_url(self, platform: str) -> Optional[str]:
        return None
