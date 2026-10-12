"""Physical box art for PS3, Wii and Wii U from GameTDB.

GameTDB names the image after the product code, which the save already
carries, so a Chinese SFO title still finds the retail box:

* PS3 ``https://art.gametdb.com/ps3/cover/<region>/<BLUS12345>.jpg``
* Wii ``https://art.gametdb.com/wii/cover3D/<region>/<RMCE01>.png``
* Wii U ``https://art.gametdb.com/wiiu/coverM/<region>/<ALZE01>.jpg``

PS4 and Xbox 360 are not on GameTDB; those stay on the libretro title lookup.
Each call returns at most eight URLs so one save cannot fan out.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from ..platforms.console_ids import (
    normalize_ps3_title_id,
    wii_cover_ids,
    wiiu_cover_ids,
)
from .providers import Artwork, ArtworkProvider

GAMETDB_ART_BASE = "https://art.gametdb.com"
_MAX_CANDIDATES = 8

# (artwork kind, file extension) in the order a gallery should try them.
_KINDS = {
    "ps3": (("cover", "jpg"),),
    "wii": (("cover3D", "png"), ("cover", "png")),
    "wiiu": (("coverM", "jpg"), ("cover3D", "png")),
}

_PS3_REGION = {"U": "US", "E": "EN", "J": "JA", "A": "ZH", "K": "KO", "H": "ZH"}
_WII_REGION = {"E": "US", "P": "EN", "J": "JA", "K": "KO", "W": "ZH"}
_WIIU_REGION = {"E": "US", "P": "EN", "J": "JA", "K": "KO"}


def _unique(items: Sequence[str]) -> Tuple[str, ...]:
    ordered: List[str] = []
    for item in items:
        if item and item not in ordered:
            ordered.append(item)
    return tuple(ordered)


def _regions_for(platform: str, code: str) -> Tuple[str, ...]:
    letter = ""
    if platform == "ps3" and len(code) >= 3:
        letter = code[2]
        preferred = _PS3_REGION.get(letter, "US")
    elif platform == "wii" and len(code) >= 4:
        letter = code[3]
        preferred = _WII_REGION.get(letter, "US")
    else:
        base = code[:4] if code else ""
        letter = base[-1] if base else ""
        preferred = _WIIU_REGION.get(letter, "US")
    return _unique((preferred, "US", "EN", "JA"))


def _ids_for(platform: str, raw: object) -> Tuple[str, ...]:
    if platform == "ps3":
        serial = normalize_ps3_title_id(raw)
        return (serial,) if serial else ()
    if platform == "wii":
        return wii_cover_ids(raw)
    if platform == "wiiu":
        return wiiu_cover_ids(raw)
    return ()


class GameTDBConsoleProvider(ArtworkProvider):
    """Portrait retail boxes indexed by PS3 / Wii / Wii U product code."""

    name = "gametdb-console"

    def __init__(self, base_url: str = GAMETDB_ART_BASE) -> None:
        self.base_url = base_url.rstrip("/")

    def supports(self, platform: str) -> bool:
        return (platform or "").strip().lower() in _KINDS

    def cover_candidates(
        self,
        platform: str,
        title_id: object = None,
        title: object = None,
    ) -> List[Artwork]:
        plat = (platform or "").strip().lower()
        kinds = _KINDS.get(plat)
        if not kinds:
            return []
        codes = _unique(
            code for raw in (title_id, title) for code in _ids_for(plat, raw)
        )
        found: List[Artwork] = []
        seen = set()
        label = str(title or title_id or "").strip()
        for code in codes:
            for region in _regions_for(plat, code):
                for kind, ext in kinds:
                    url = f"{self.base_url}/{plat}/{kind}/{region}/{code}.{ext}"
                    if url in seen:
                        continue
                    seen.add(url)
                    found.append(
                        Artwork(
                            url=url,
                            filename=f"{code}.{ext}",
                            provider=self.name,
                            platform=plat,
                            canonical_title=label or code,
                        )
                    )
                    if len(found) >= _MAX_CANDIDATES:
                        return found
        return found

    def cover_for(self, platform: str, title: str) -> Optional[Artwork]:
        candidates = self.cover_candidates(platform, title, title)
        return candidates[0] if candidates else None

    def find_cover(self, metadata) -> Optional[Artwork]:
        if metadata is None:
            return None
        if isinstance(metadata, dict):
            platform = metadata.get("platform")
            title_id = metadata.get("title_id")
            title = metadata.get("title") or metadata.get("canonical_title")
        else:
            platform = getattr(metadata, "platform", None)
            title_id = getattr(metadata, "title_id", None)
            title = getattr(metadata, "canonical_title", None) or getattr(
                metadata, "title", None
            )
        candidates = self.cover_candidates(platform, title_id, title)
        return candidates[0] if candidates else None
