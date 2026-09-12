"""Map 3DS Checkpoint short IDs to eShop names for libretro boxart.

Checkpoint folders look like ``0x00306 MARIO KART 7``. The hex prefix is the
unique title fragment; shifting it back yields the 16-digit Title ID
``0004000000030600``. Official eShop lists (3dsdb) then give a cleaner English
name than the ALL-CAPS folder, which Named_Boxarts can actually hit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..persistence import atomic_write_json

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_HTML_RE = re.compile(r"<[^>]+>")
_TRADEMARK_RE = re.compile(r"[™®©]")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# 3dsdb regional dumps. Base 3DS games use TitleID prefix 00040000.
_3DSDB_LISTS: Tuple[Tuple[str, str], ...] = (
    ("USA", "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_US.json"),
    ("Europe", "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_GB.json"),
    ("Japan", "https://raw.githubusercontent.com/hax0kartik/3dsdb/master/jsons/list_JP.json"),
)
_BASE_GAME_PREFIX = "00040000"
INDEX_NAME = "3ds-title-index.json"


def expand_3ds_title_id(value: object) -> Optional[str]:
    """Return a 16-digit lowercase Title ID, or ``None`` if ``value`` is junk."""
    text = str(value or "").strip().lower().replace("0x", "")
    if not text or not _HEX_RE.fullmatch(text):
        return None
    number = int(text, 16)
    if len(text) >= 16:
        return f"{number:016x}"
    return f"{_BASE_GAME_PREFIX}{(number << 8):08x}"


def clean_eshop_name(name: str) -> str:
    """Strip TM marks, HTML, and a trailing Japanese parenthetical."""
    text = _HTML_RE.sub(" ", str(name or ""))
    text = _TRADEMARK_RE.sub("", text)
    if "(" in text:
        head, rest = text.split("(", 1)
        if _CJK_RE.search(rest):
            text = head
    return re.sub(r"\s+", " ", text).strip(" -_")


def titles_from_3dsdb_lists(lists: Sequence[Tuple[str, Any]]) -> Dict[str, List[Dict[str, str]]]:
    """Build ``title_id -> [{name, region}, ...]`` from 3dsdb JSON lists."""
    catalog: Dict[str, List[Dict[str, str]]] = {}
    seen = set()
    for region, payload in lists:
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            tid = expand_3ds_title_id(item.get("TitleID"))
            if not tid or not tid.startswith(_BASE_GAME_PREFIX):
                continue
            name = clean_eshop_name(str(item.get("Name") or ""))
            if not name:
                continue
            key = (tid, name, region)
            if key in seen:
                continue
            seen.add(key)
            catalog.setdefault(tid, []).append({"name": name, "region": region})
    return catalog


def title_candidates_for_id(
    catalog: Dict[str, List[Dict[str, str]]], title_id: object
) -> Tuple[str, ...]:
    """Libretro-oriented names for a Checkpoint / Title ID, USA first."""
    tid = expand_3ds_title_id(title_id)
    if not tid:
        return ()
    records = catalog.get(tid) or []
    ordered: List[str] = []

    def add(value: str) -> None:
        cleaned = value.strip()
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)

    region_rank = {"USA": 0, "Europe": 1, "Japan": 2}
    records = sorted(records, key=lambda rec: region_rank.get(rec.get("region", ""), 9))
    for rec in records:
        name = rec.get("name") or ""
        region = rec.get("region") or ""
        add(name)
        if region and f"({region})" not in name:
            add(f"{name} ({region})")
    return tuple(ordered)


def index_path(cache_root: Optional[Path]) -> Optional[Path]:
    if cache_root is None:
        return None
    return Path(cache_root) / INDEX_NAME


def load_catalog(cache_root: Optional[Path]) -> Dict[str, List[Dict[str, str]]]:
    path = index_path(cache_root)
    if path is None:
        return {}
    try:
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    games = raw.get("games") if isinstance(raw, dict) else None
    if not isinstance(games, dict):
        return {}
    out: Dict[str, List[Dict[str, str]]] = {}
    for tid, records in games.items():
        if not isinstance(records, list):
            continue
        cleaned = [rec for rec in records if isinstance(rec, dict) and rec.get("name")]
        if cleaned:
            out[str(tid).lower()] = cleaned
    return out


def store_catalog(cache_root: Optional[Path], catalog: Dict[str, List[Dict[str, str]]]) -> bool:
    path = index_path(cache_root)
    if path is None:
        return False
    return bool(atomic_write_json(path, {"games": catalog}))


def fetch_3dsdb_catalog(urlopen: Callable[..., Any], timeout: float = 20.0) -> Dict[str, List[Dict[str, str]]]:
    """Download regional 3dsdb lists and compact them. Empty dict on failure."""
    loaded: List[Tuple[str, Any]] = []
    for region, url in _3DSDB_LISTS:
        try:
            response = urlopen(url, timeout=timeout)
        except Exception:  # noqa: BLE001
            continue
        try:
            raw = response.read()
            payload = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except Exception:  # noqa: BLE001
            payload = None
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    pass
        if payload is not None:
            loaded.append((region, payload))
    return titles_from_3dsdb_lists(loaded)
