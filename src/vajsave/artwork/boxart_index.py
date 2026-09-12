"""``Named_Boxarts`` directory listing: unique filename fallback for box art.

The libretro thumbnail server is an Apache autoindex: when a generated candidate
filename 404s, the directory page already lists every real file name.  Instead of
fanning out into more guessed variants, :func:`unique_boxart_match` accepts a
listing entry only when **exactly one** is consistent with the query -- two
region variants of the same base title are ambiguous and are rejected rather
than picked arbitrarily.

The module also owns the one Checkpoint naming rule that changes the libretro
*system*: a 3DS save with **no** usable 3DS title id whose display name is a DS
cartridge code plus title (``AZEJ Kirby Super Star Ultra``) is a DS save and must
be looked up under ``Nintendo - Nintendo DS``, never the 3DS folder.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Optional, Sequence, Tuple
from urllib.parse import unquote

from .title_ids import expand_3ds_title_id

# A single directory entry link, e.g. ``<a href="Kirby%20...png">``.
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Non-alphanumerics become separators before token comparison.
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# A DS cartridge Checkpoint folder: 4-char uppercase game code + a title.
_DS_CARTRIDGE_RE = re.compile(r"^([0-9A-Z]{4})\s+(\S.*)$")
# Never buffer more than this from a listing page (the largest real system
# listing is a couple of MB).
MAX_LISTING_BYTES = 16 * 1024 * 1024


def parse_boxart_listing(html: Any) -> Tuple[str, ...]:
    """Return the unique ``*.png`` leaf names in an autoindex page, in order.

    Percent-encoded hrefs are decoded so callers get the real file name.  Any
    non-PNG link (the ``Parent Directory`` row, ``?C=N;O=D`` sort links, stray
    text files) is dropped; duplicates collapse.
    """
    if not html:
        return ()
    if isinstance(html, (bytes, bytearray)):
        html = bytes(html).decode("utf-8", errors="replace")
    names = []
    seen = set()
    for raw in _HREF_RE.findall(str(html)):
        leaf = unquote(raw).rsplit("/", 1)[-1]
        if not leaf.lower().endswith(".png"):
            continue
        if leaf in seen:
            continue
        seen.add(leaf)
        names.append(leaf)
    return tuple(names)


def _fold_ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_boxart_name(name: Any) -> str:
    """Case/punctuation/diacritic-insensitive form used for comparison."""
    folded = _fold_ascii(str(name or "")).lower()
    return " ".join(_NON_ALNUM_RE.sub(" ", folded).split())


def _tokens(value: Any) -> Tuple[str, ...]:
    normalized = normalize_boxart_name(value)
    return tuple(normalized.split()) if normalized else ()


def _strip_png(name: str) -> str:
    return name[:-4] if name.lower().endswith(".png") else name


def _contains(haystack: Tuple[str, ...], needle: Tuple[str, ...]) -> bool:
    """True when ``needle`` appears as a contiguous run of ``haystack`` tokens."""
    if not needle or len(needle) > len(haystack):
        return False
    window = len(needle)
    return any(haystack[i : i + window] == needle for i in range(len(haystack) - window + 1))


def unique_boxart_match(
    filenames: Sequence[str], query: Any
) -> Optional[str]:
    """The one listing entry consistent with ``query``, else ``None``.

    A match is token-containment based so the query appears inside a longer
    real file name (``Kid Icarus Uprising`` -> ``Kid Icarus - Uprising (USA).png``)
    or a region/language suffix still matches the bare Checkpoint title.  Zero or
    more than one match returns ``None``: an ambiguous query is never resolved by
    guessing.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return None
    matches = []
    for name in filenames:
        if not name:
            continue
        entry_tokens = _tokens(_strip_png(str(name)))
        if not entry_tokens:
            continue
        if _contains(entry_tokens, query_tokens) or _contains(
            query_tokens, entry_tokens
        ):
            matches.append(name)
    if len(matches) == 1:
        return matches[0]
    return None


def fetch_boxart_listing(
    urlopen: Callable[..., Any], url: Optional[str], *, timeout: float = 20.0
) -> Tuple[str, ...]:
    """GET an autoindex page and return its ``*.png`` names; ``()`` on failure."""
    if not url:
        return ()
    try:
        response = urlopen(url, timeout=timeout)
    except Exception:  # noqa: BLE001 - offline/404/timeout all mean "no listing"
        return ()
    try:
        status = getattr(response, "status", None)
        if status is None:
            status = getattr(response, "code", None)
        if status is not None and int(status) != 200:
            return ()
        raw = response.read(MAX_LISTING_BYTES + 1)
        if raw is None or len(raw) > MAX_LISTING_BYTES:
            return ()
    except Exception:  # noqa: BLE001
        return ()
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    return parse_boxart_listing(raw)


def strip_ds_cartridge_code(name: Any) -> Optional[str]:
    """Return the title in ``AZEJ Kirby Super Star Ultra``, or ``None``.

    Only an all-uppercase 4-character game code counts, so a normal mixed-case
    3DS title (``Cube Creator 3D``) is never mistaken for a cartridge code.
    """
    match = _DS_CARTRIDGE_RE.match(str(name or "").strip())
    if not match:
        return None
    title = match.group(2).strip()
    title = title.strip(" -_")
    return title or None


def resolve_boxart_system(
    platform: Any, title: Any, title_id: Any = None
) -> Tuple[str, str]:
    """Resolve the ``(platform, query_title)`` a Checkpoint/SFO entry maps to.

    A 3DS entry without a usable 3DS title id whose display name is a DS
    cartridge (``XXXX Title``) belongs to ``nds``; everything else is unchanged.
    """
    plat = str(platform or "").strip().lower()
    text = str(title or "")
    if plat == "3ds" and expand_3ds_title_id(title_id) is None:
        ds_title = strip_ds_cartridge_code(text)
        if ds_title:
            return ("nds", ds_title)
    return (plat, text)


__all__ = [
    "fetch_boxart_listing",
    "normalize_boxart_name",
    "parse_boxart_listing",
    "resolve_boxart_system",
    "strip_ds_cartridge_code",
    "unique_boxart_match",
]
