"""Nintendo Switch artwork provider and GameTDB physical box art integration.

GameTDB hosts authentic physical retail box covers for Nintendo Switch games
under:
    https://art.gametdb.com/switch/coverM/<REGION>/<ID>.jpg

where ``<ID>`` is the 4- or 5-character serial (e.g. ``AAACA`` for Super Mario
Odyssey) and ``<REGION>`` is the regional release (``US``, ``EN``, ``JA``,
``ZH``, etc.). The ``coverM`` resolution is 352x570, which is portrait aspect
matching the physical Switch retail plastic game case.

If a game has no physical cartridge release (e.g. digital-only indie titles),
or is missing from GameTDB, the fallback provider retrieves the official
square icon from Nlib-API using the 16-character hexadecimal Title ID:
    https://api.nlib.cc/nx/<TITLE_ID>/icon
"""

from __future__ import annotations

import gzip
import json
import re
import threading
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .providers import Artwork, ArtworkProvider

GAMETDB_COVER_BASE = "https://art.gametdb.com/switch/coverM"
NLIB_API_BASE = "https://api.nlib.cc/nx"
DEFAULT_CATALOG_FILE = "gametdb_switch.json.gz"
DEFAULT_TITLES_FILE = "switch_titles.json.gz"

_HEX_TITLE_ID_RE = re.compile(r"^(?:0x)?([0-9a-fA-F]{14,16})$")

# Curated lookup dictionary of well-known Switch Title IDs to canonical English game titles
KNOWN_SWITCH_TITLES: Dict[str, str] = {
    "010049900F556000": "Super Mario 3D All-Stars",
    "01006BB00C6F0000": "The Legend of Zelda: Link's Awakening",
    "01006A800016E000": "Super Smash Bros. Ultimate",
    "0100000000010000": "Super Mario Odyssey",
    "01007EF00011E000": "The Legend of Zelda: Breath of the Wild",
    "0100F2C0115B6000": "The Legend of Zelda: Tears of the Kingdom",
    "0100030003CFA000": "Mario Kart 8 Deluxe",
    "01006F8002326000": "Animal Crossing: New Horizons",
    "0100ABF008968000": "Pokémon Sword",
    "01008DB008C2C000": "Pokémon Shield",
    "0100000011D90000": "Pokémon Legends: Arceus",
    "01008F6008C5E000": "Pokémon Scarlet",
    "0100A3D008EA6000": "Pokémon Violet",
    "010028600EBDA000": "Super Mario 3D World + Bowser's Fury",
    "01000A10041EA000": "The Elder Scrolls V: Skyrim",
    "010036B0034E4000": "Super Mario Party",
    "0100D8701267E000": "Mario Party Superstars",
    "0100E26011DBE000": "Super Mario Bros. Wonder",
    "0100C9A00ECE6000": "Metroid Dread",
    "0100C5E00D774000": "Metroid Prime Remastered",
    "010000500D0FC000": "Kirby and the Forgotten Land",
    "0100B04011742000": "Pikmin 4",
    "010084A008C4A000": "Luigi's Mansion 3",
    "01009B90006DC000": "Splatoon 2",
    "0100C2500FC20000": "Splatoon 3",
    "010040600C80E000": "Fire Emblem: Three Houses",
    "0100C6D015C7E000": "Fire Emblem Engage",
    "01007A600B04E000": "Xenoblade Chronicles Definitive Edition",
    "0100E95004038000": "Xenoblade Chronicles 2",
    "01007460107BF000": "Xenoblade Chronicles 3",
    "010069401ADB8000": "Unicorn Overlord",
    "01005CA01580E000": "Persona 5 Royal",
    "01004E500DB9E000": "Summer Sweetheart",
    "01003C7017BB4000": "Aiyoku no Eustia: Angel's Blessing",
    "01004AB00A260000": "DARK SOULS: REMASTERED",
    "01004A4010FEA000": "Bayonetta 3",
    "01002EF01A316000": "Brotato",
    "0100C1F0051B6000": "Donkey Kong Country: Tropical Freeze",
    "010042000A986000": "DRAGON QUEST BUILDERS 2",
    "01006B601380E000": "Kirby's Return to Dream Land Deluxe",
    "0100EA80032EA000": "New Super Mario Bros. U Deluxe",
    "0100F28011892000": "Overcooked! All You Can Eat",
    "010026300BA4A000": "Slay the Spire",
    "010089A0197E4000": "Vampire Survivors",
    "0100563010E0C000": "WarioWare: Get It Together!",
    "01006000040C2000": "Yoshi's Crafted World",
    "0100F5001C12C000": "ASTLIBRA Revision",
    "0100D15016256000": "Prince of Persia: The Lost Crown",
    "01007A3009184000": "Princess Peach: Showtime!",
    "0100A8B01E0C8000": "Shin chan: Shiro and the Coal Town",
    "0100965017338000": "Super Mario Party Jamboree",
    "01008CF01BAAC000": "The Legend of Zelda: Echoes of Wisdom",
}

# Regional / translated aliases mapping to canonical Title IDs
KNOWN_TITLE_ALIASES: Dict[str, str] = {
    "유니콘 오버로드": "010069401ADB8000",
    "페르소ナ 5 더 로열": "01005CA01580E000",
    "페르소나 5 더 로열": "01005CA01580E000",
    "秽翼的尤斯蒂娅": "01003C7017BB4000",
    "穢翼のユースティア": "01003C7017BB4000",
    "甜蜜夏日": "01004E500DB9E000",
    "甜蜜夏日 ～perfect edition～": "01004E500DB9E000",
    "summer sweetheart": "01004E500DB9E000",
    "unicorn overlord": "010069401ADB8000",
    "persona 5 royal": "01005CA01580E000",
}

_SWITCH_TITLES_CATALOG: Optional[Dict[str, str]] = None
_SWITCH_TITLES_LOCK = threading.RLock()
_SWITCH_REVERSE_CATALOG: Optional[Dict[str, str]] = None


def _load_switch_titles_catalog() -> Dict[str, str]:
    global _SWITCH_TITLES_CATALOG
    if _SWITCH_TITLES_CATALOG is None:
        with _SWITCH_TITLES_LOCK:
            if _SWITCH_TITLES_CATALOG is None:
                path = Path(__file__).parent / DEFAULT_TITLES_FILE
                cat: Dict[str, str] = {}
                if path.is_file():
                    try:
                        with gzip.open(path, "rb") as f:
                            cat = json.loads(f.read().decode("utf-8"))
                    except Exception:
                        pass
                _SWITCH_TITLES_CATALOG = cat
    return _SWITCH_TITLES_CATALOG


def _load_switch_reverse_catalog() -> Dict[str, str]:
    global _SWITCH_REVERSE_CATALOG
    if _SWITCH_REVERSE_CATALOG is None:
        with _SWITCH_TITLES_LOCK:
            if _SWITCH_REVERSE_CATALOG is None:
                rev: Dict[str, str] = {}
                cat = _load_switch_titles_catalog()
                # 1. Base pass: exact normalized names from database
                for tid, name in cat.items():
                    norm_k = normalize_switch_title(name)
                    if norm_k and norm_k not in rev:
                        rev[norm_k] = tid
                # Overwrite/seed with curated KNOWN_SWITCH_TITLES
                for tid, name in KNOWN_SWITCH_TITLES.items():
                    norm_k = normalize_switch_title(name)
                    if norm_k:
                        rev[norm_k] = tid
                # 2. Second pass: prefix before " - ", " – ", ": ", or ":"
                for tid, name in cat.items():
                    for sep in (" - ", " – ", ": ", ":"):
                        if sep in name:
                            prefix = name.split(sep, 1)[0].strip()
                            norm_p = normalize_switch_title(prefix)
                            if len(norm_p) >= 4 and norm_p not in rev:
                                rev[norm_p] = tid
                _SWITCH_REVERSE_CATALOG = rev
    return _SWITCH_REVERSE_CATALOG


def _clean_switch_title_id(raw: Optional[object]) -> Optional[str]:
    """Normalize a raw string or number into a 16-hex Switch Title ID (e.g. 0100...)."""
    if not raw:
        return None
    raw_s = re.sub(r"^(?:0?x)?", "", str(raw).strip(), flags=re.IGNORECASE)
    clean_id = re.sub(r"[^0-9a-fA-F]", "", raw_s).upper()
    if len(clean_id) < 16 and clean_id.startswith("0100"):
        clean_id = clean_id.ljust(16, "0")
    return clean_id if len(clean_id) == 16 else None


_SWITCH_TID_TO_GAMETDB: Optional[Dict[str, Tuple[str, str]]] = None
_SWITCH_TID_TO_GAMETDB_LOCK = threading.RLock()


def _load_switch_tid_to_gametdb_map() -> Dict[str, Tuple[str, str]]:
    global _SWITCH_TID_TO_GAMETDB
    if _SWITCH_TID_TO_GAMETDB is None:
        with _SWITCH_TID_TO_GAMETDB_LOCK:
            if _SWITCH_TID_TO_GAMETDB is None:
                mapping: Dict[str, Tuple[str, str]] = {}
                gt_catalog = GameTDBSwitchProvider().catalog
                if gt_catalog:
                    # 1. Curated titles first
                    for tid, name in KNOWN_SWITCH_TITLES.items():
                        clean_tid = _clean_switch_title_id(tid)
                        if not clean_tid:
                            continue
                        for c in switch_title_candidates(name, clean_tid):
                            k = normalize_switch_title(c)
                            if k and k in gt_catalog:
                                hit = gt_catalog[k]
                                if ":" in hit:
                                    gid, reg = hit.split(":", 1)
                                    mapping[clean_tid] = (gid, reg)
                                    break
                    # 2. Entire switch titles catalog
                    st_catalog = _load_switch_titles_catalog()
                    for tid, name in st_catalog.items():
                        clean_tid = _clean_switch_title_id(tid)
                        if not clean_tid or clean_tid in mapping:
                            continue
                        k = normalize_switch_title(name)
                        if k and k in gt_catalog:
                            hit = gt_catalog[k]
                            if ":" in hit:
                                gid, reg = hit.split(":", 1)
                                mapping[clean_tid] = (gid, reg)
                                continue
                        for sep in (" - ", " – ", ": ", ":"):
                            if sep in name:
                                prefix = name.split(sep, 1)[0].strip()
                                pk = normalize_switch_title(prefix)
                                if len(pk) >= 4 and pk in gt_catalog:
                                    hit = gt_catalog[pk]
                                    if ":" in hit:
                                        gid, reg = hit.split(":", 1)
                                        mapping[clean_tid] = (gid, reg)
                                        break
                _SWITCH_TID_TO_GAMETDB = mapping
    return _SWITCH_TID_TO_GAMETDB


def get_gametdb_id_for_title_id(title_id: Optional[str]) -> Optional[Tuple[str, str]]:
    """Directly resolve a 16-hex Switch Title ID to its GameTDB (id, region)."""
    clean_id = _clean_switch_title_id(title_id)
    if not clean_id:
        return None
    tid_map = _load_switch_tid_to_gametdb_map()
    if clean_id in tid_map:
        return tid_map[clean_id]
    # Dynamic fallback: check title name if not in precomputed map
    title = get_switch_title_for_id(clean_id)
    if title:
        gt_catalog = GameTDBSwitchProvider().catalog
        for c in switch_title_candidates(title, clean_id):
            k = normalize_switch_title(c)
            if k and k in gt_catalog:
                hit = gt_catalog[k]
                if ":" in hit:
                    gid, reg = hit.split(":", 1)
                    tid_map[clean_id] = (gid, reg)
                    return (gid, reg)
    return None


def get_switch_title_for_id(title_id: Optional[str]) -> Optional[str]:
    """Resolve a 16-hex Switch Title ID to its canonical game title."""
    clean_id = _clean_switch_title_id(title_id)
    if not clean_id:
        return None
    if clean_id in KNOWN_SWITCH_TITLES:
        return KNOWN_SWITCH_TITLES[clean_id]
    cat = _load_switch_titles_catalog()
    return cat.get(clean_id) or KNOWN_SWITCH_TITLES.get(clean_id)


def normalize_switch_title(text: object) -> str:
    """ASCII-fold and strip punctuation/whitespace for fuzzy title matching."""
    raw = str(text or "")
    raw = re.sub(r"[™®©]", "", raw)
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]", "", folded.lower())


def get_switch_id_for_title(title: Optional[str]) -> Optional[str]:
    """Resolve a game title string to its 16-hex Switch Title ID."""
    if not title:
        return None
    raw = str(title).strip()
    raw_lower = raw.lower()
    if raw_lower in KNOWN_TITLE_ALIASES:
        return KNOWN_TITLE_ALIASES[raw_lower]
    for alias_name, tid in KNOWN_TITLE_ALIASES.items():
        if alias_name in raw_lower:
            return tid

    cands = switch_title_candidates(title)
    # Check known titles first
    for cand in cands:
        key = normalize_switch_title(cand)
        if not key:
            continue
        for tid, name in KNOWN_SWITCH_TITLES.items():
            if normalize_switch_title(name) == key:
                return tid

    rev_catalog = _load_switch_reverse_catalog()
    for cand in cands:
        key = normalize_switch_title(cand)
        if key and key in rev_catalog:
            return rev_catalog[key]
    return None


def switch_title_candidates(title: object, title_id: Optional[object] = None) -> Tuple[str, ...]:
    """Generate ordered candidate names for matching Switch game titles.

    Strips common tags (e.g. ``[Starter Pack]``, ``(v1.0)``) and punctuation,
    while keeping the original title first. If title is a bare Title ID, attempts
    resolution using the local Title ID database.
    """
    raw = str(title or "").strip()
    candidates: List[str] = []

    def add(cand: str) -> None:
        cleaned = " ".join(cand.split()).strip()
        if cleaned and cleaned not in candidates:
            # Skip candidates that are just bare hex Title IDs
            if not _HEX_TITLE_ID_RE.match(cleaned):
                candidates.append(cleaned)

    if title_id:
        resolved = get_switch_title_for_id(str(title_id).strip())
        if resolved:
            add(resolved)

    if raw:
        add(raw)
        # Strip square bracket tags: "Super Mario Odyssey [Starter Pack]"
        no_brackets = re.sub(r"\[.*?\]", "", raw).strip()
        if no_brackets:
            add(no_brackets)
        # Strip parentheses tags: "Bayonetta (USA)"
        no_parens = re.sub(r"\(.*?\)", "", raw).strip()
        if no_parens:
            add(no_parens)
        # Strip both
        stripped = re.sub(r"\[.*?\]|\(.*?\)", "", raw).strip()
        if stripped:
            add(stripped)
        # Split on hyphen separator: "Brotato - Nintendo Switch Edition"
        if " - " in raw:
            add(raw.split(" - ", 1)[0].strip())
        # Split on colon or hyphen if present: "The Legend of Zelda: Breath of the Wild"
        if ":" in raw:
            add(raw.split(":", 1)[0].strip())
            add(raw.replace(":", " ").strip())

    return tuple(candidates)


class GameTDBSwitchProvider(ArtworkProvider):
    """Provides authentic physical Switch box art (coverM) from GameTDB."""

    name = "gametdb"

    def __init__(
        self,
        catalog_path: Optional[Union[Path, str]] = None,
        base_url: str = GAMETDB_COVER_BASE,
    ) -> None:
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self.base_url = base_url.rstrip("/")
        self._catalog: Optional[Dict[str, str]] = None
        self._lock = threading.Lock()

    def supports(self, platform: str) -> bool:
        return (platform or "").strip().lower() == "switch"

    def _default_catalog_path(self) -> Path:
        return Path(__file__).parent / DEFAULT_CATALOG_FILE

    def _load_catalog(self) -> Dict[str, str]:
        path = self.catalog_path or self._default_catalog_path()
        if not path.is_file():
            return {}
        try:
            if path.suffix == ".gz" or str(path).endswith(".json.gz"):
                with gzip.open(path, "rb") as f:
                    return json.loads(f.read().decode("utf-8"))
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, gzip.BadGzipFile):
            return {}

    @property
    def catalog(self) -> Dict[str, str]:
        if self._catalog is None:
            with self._lock:
                if self._catalog is None:
                    self._catalog = self._load_catalog()
        return self._catalog

    def lookup_by_title_id(self, title_id: str) -> Optional[Tuple[str, str]]:
        """Look up GameTDB (id, region) directly from a Switch Title ID."""
        clean_id = _clean_switch_title_id(title_id)
        if not clean_id:
            return None
        if self.catalog_path is None:
            return get_gametdb_id_for_title_id(clean_id)
        cat = self.catalog
        if not cat:
            return None
        title = get_switch_title_for_id(clean_id)
        if title:
            for cand in switch_title_candidates(title, clean_id):
                key = normalize_switch_title(cand)
                if key and key in cat:
                    hit = cat[key]
                    if ":" in hit:
                        gid, reg = hit.split(":", 1)
                        return (gid, reg)
        return None

    def lookup_game(self, title: str) -> Optional[Tuple[str, str]]:
        """Look up GameTDB (id, region) for a title; returns None if not found."""
        if not title:
            return None
        clean_id = _clean_switch_title_id(title)
        if clean_id:
            hit = self.lookup_by_title_id(clean_id)
            if hit:
                return hit
        cat = self.catalog
        if not cat:
            return None
        candidates = switch_title_candidates(title)
        for cand in candidates:
            key = normalize_switch_title(cand)
            if not key:
                continue
            hit = cat.get(key)
            if hit and ":" in hit:
                gid, reg = hit.split(":", 1)
                return (gid, reg)
        return None

    def cover_for_title_id(self, title_id: str) -> Optional[Artwork]:
        """Build GameTDB physical box artwork directly from a Switch Title ID."""
        match = self.lookup_by_title_id(title_id)
        if not match:
            return None
        gid, reg = match
        url = f"{self.base_url}/{reg}/{gid}.jpg"
        canonical = get_switch_title_for_id(title_id) or str(title_id)
        return Artwork(
            url=url,
            filename=f"{gid}.jpg",
            provider=self.name,
            platform="switch",
            canonical_title=canonical,
        )

    def cover_candidates_for_title_id(self, title_id: str) -> List[Artwork]:
        """Return Artwork candidates for a Title ID trying primary region then US/EN/JA/ZH."""
        match = self.lookup_by_title_id(title_id)
        if not match:
            return []
        gid, reg = match
        canonical = get_switch_title_for_id(title_id) or str(title_id)
        artworks: List[Artwork] = []
        seen_regions = set()
        for r in (reg, "US", "EN", "JA", "ZH"):
            if r in seen_regions:
                continue
            seen_regions.add(r)
            artworks.append(
                Artwork(
                    url=f"{self.base_url}/{r}/{gid}.jpg",
                    filename=f"{gid}_{r}.jpg",
                    provider=self.name,
                    platform="switch",
                    canonical_title=canonical,
                )
            )
        return artworks

    def cover_for(self, platform: str, title: str) -> Optional[Artwork]:
        if not self.supports(platform) or not title:
            return None
        clean_id = _clean_switch_title_id(title)
        if clean_id:
            art = self.cover_for_title_id(clean_id)
            if art:
                return art
        match = self.lookup_game(title)
        if not match:
            return None
        gid, reg = match
        url = f"{self.base_url}/{reg}/{gid}.jpg"
        return Artwork(
            url=url,
            filename=f"{gid}.jpg",
            provider=self.name,
            platform="switch",
            canonical_title=title,
        )

    def cover_candidates(self, title: str) -> List[Artwork]:
        """Return Artwork candidates trying primary region then US/EN/JA fallbacks."""
        if not title:
            return []
        clean_id = _clean_switch_title_id(title)
        if clean_id:
            cands = self.cover_candidates_for_title_id(clean_id)
            if cands:
                return cands
        match = self.lookup_game(title)
        if not match:
            return []
        gid, reg = match
        artworks: List[Artwork] = []
        seen_regions = set()
        for r in (reg, "US", "EN", "JA", "ZH"):
            if r in seen_regions:
                continue
            seen_regions.add(r)
            artworks.append(
                Artwork(
                    url=f"{self.base_url}/{r}/{gid}.jpg",
                    filename=f"{gid}_{r}.jpg",
                    provider=self.name,
                    platform="switch",
                    canonical_title=title,
                )
            )
        return artworks


class NlibSwitchProvider(ArtworkProvider):
    """Fallback provider fetching official Switch square icons via Title ID from Nlib-API."""

    name = "nlib"

    def __init__(self, base_url: str = NLIB_API_BASE) -> None:
        self.base_url = base_url.rstrip("/")

    def supports(self, platform: str) -> bool:
        return (platform or "").strip().lower() == "switch"

    def cover_for(self, platform: str, title: str) -> Optional[Artwork]:
        if not self.supports(platform) or not title:
            return None
        m = _HEX_TITLE_ID_RE.match(title.strip())
        if m:
            tid = m.group(1).upper()
            return self.cover_for_title_id(tid)
        return None

    def cover_for_title_id(self, title_id: str) -> Optional[Artwork]:
        clean_id = re.sub(r"[^0-9a-fA-F]", "", str(title_id or "")).upper()
        if len(clean_id) != 16:
            return None
        url = f"{self.base_url}/{clean_id}/icon"
        return Artwork(
            url=url,
            filename=f"{clean_id}.jpg",
            provider=self.name,
            platform="switch",
            canonical_title=clean_id,
        )

    @staticmethod
    def resolve_title_name(
        urlopen: Callable[..., Any], title_id: str, timeout: float = 5.0
    ) -> Optional[str]:
        """Resolve a Title ID to its canonical English name via local db or Nlib-API."""
        clean_id = re.sub(r"[^0-9a-fA-F]", "", str(title_id or "")).upper()
        if len(clean_id) < 16 and clean_id.startswith("0100"):
            clean_id = clean_id.ljust(16, "0")
        if len(clean_id) != 16:
            return None
        # Fast path: check local database first!
        known = get_switch_title_for_id(clean_id)
        if known:
            return known
        url = f"{NLIB_API_BASE}/{clean_id}?lang=en&fields=name"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36 vaj-save/1.0"
                    )
                },
            )
            try:
                resp = urlopen(req, timeout=timeout)
            except (TypeError, AttributeError):
                resp = urlopen(url, timeout=timeout)
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            name = payload.get("name")
            return str(name).strip() if name else None
        except Exception:  # noqa: BLE001
            return None


__all__ = [
    "GAMETDB_COVER_BASE",
    "NLIB_API_BASE",
    "DEFAULT_CATALOG_FILE",
    "DEFAULT_TITLES_FILE",
    "KNOWN_SWITCH_TITLES",
    "KNOWN_TITLE_ALIASES",
    "GameTDBSwitchProvider",
    "NlibSwitchProvider",
    "_clean_switch_title_id",
    "get_gametdb_id_for_title_id",
    "get_switch_id_for_title",
    "get_switch_title_for_id",
    "normalize_switch_title",
    "switch_title_candidates",
]
