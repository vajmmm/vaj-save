"""``Named_Boxarts`` directory listing: unique filename fallback for box art.

The libretro thumbnail server is an Apache autoindex: when a generated candidate
filename 404s, the directory page already lists every real file name.  Instead of
fanning out into more guessed variants, :func:`unique_boxart_match` accepts a
listing entry only when it is unambiguous: several region/language variants of
**one** normalised title resolve to the USA release, while two genuinely
different titles stay rejected rather than picked arbitrarily.  When even that
strict matcher cannot line up a candidate, :func:`concatenation_boxart_candidates`
offers the precise subset whose words the query joins into one token (``3 G``
-> ``3G``) and :func:`loose_boxart_candidates` a scored, capped word-overlap pool
(stopwords and parenthetical glosses ignored, concatenated words ranked first)
to the optional LLM so a real file name whose words merely overlap the query can
still resolve.  The service offers the concatenation pool before the strict
ambiguous pools, so a longer title that merely repeats the query words
(``Monster Hunter 3`` -> ``Monster Hunter 3 Ultimate``) cannot preempt a genuine
concatenated title (``Monster Hunter 3G``).

The module also owns the one Checkpoint naming rule that changes the libretro
*system*: a 3DS save with **no** usable 3DS title id whose display name is a DS
cartridge code plus title (``AZEJ Kirby Super Star Ultra``) is a DS save and must
be looked up under ``Nintendo - Nintendo DS``, never the 3DS folder.  Only a
*real* DS serial counts, so a 3DS title whose first word merely looks like one
(``NANO Assault``) stays on 3DS.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any, Callable, FrozenSet, Optional, Sequence, Tuple
from urllib.parse import unquote

from ..identity.naming import extract_region, normalize_title
from ..metadata.paths import bundled_libretro_dir
from .title_ids import expand_3ds_title_id

# Non-nested ``(...)`` / ``[...]`` groups (region tags, pronunciation glosses).
_PAREN_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")

# A single directory entry link, e.g. ``<a href="Kirby%20...png">``.
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
# Non-alphanumerics become separators before token comparison.
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")
# A DS cartridge Checkpoint folder: 4-char uppercase game code + a title.
_DS_CARTRIDGE_RE = re.compile(r"^([0-9A-Z]{4})\s+(\S.*)$")
# The fourth character of a DS game code is its region/language marker.  ``O``
# is not one, so ``NANO`` (from the 3DS title "Nano Assault") is not a code.
# This is only the fallback when the shipped serial index is unavailable; with
# the index present, *real* serials are authoritative.
_DS_REGION_MARKERS: FrozenSet[str] = frozenset("ABCDEFGHIJKLMNPQRSTUVWXYZ")
_NDS_SERIAL_INDEX_NAME = "nds.json"
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


# Preference order for the region/language variants of one title.  USA wins; an
# entry with no recognisable region tag is only used when nothing is tagged.
_REGION_PREFERENCE = {"USA": 0, "World": 1, "Europe": 2, "Japan": 3}
_UNKNOWN_REGION_RANK = 5
_UNREGIONED_RANK = 9


def _base_title_key(name: str) -> str:
    """Region/language/extension-free key grouping one title's variants."""
    return normalize_title(_strip_png(name))


def _region_rank(name: str) -> Tuple[int, int]:
    region = extract_region(_strip_png(name))
    if region is None:
        return (1, _UNREGIONED_RANK)
    return (0, _REGION_PREFERENCE.get(region, _UNKNOWN_REGION_RANK))


def _matching_entries(filenames: Sequence[str], query: Any) -> list:
    """Listing entries whose tokens and ``query`` contain one another."""
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
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
    return matches


def _resolve_unique_match(matches: Sequence[str]) -> Optional[str]:
    """Reduce a set of matches to one pick, or ``None`` when genuinely ambiguous.

    A single entry wins outright. Several entries are accepted only when they
    are all region/language variants of **one** normalised title; the USA
    release is then preferred. Two different titles (or two equally-ranked
    region variants) stay unresolved.
    """
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    keys = {_base_title_key(name) for name in matches}
    if len(keys) != 1 or "" in keys:
        return None
    ranked = sorted(matches, key=_region_rank)
    best_rank = _region_rank(ranked[0])
    if sum(1 for name in matches if _region_rank(name) == best_rank) != 1:
        return None
    return ranked[0]


def unique_boxart_match(
    filenames: Sequence[str], query: Any
) -> Optional[str]:
    """The one listing entry consistent with ``query``, else ``None``.

    A match is token-containment based so the query appears inside a longer
    real file name (``Kid Icarus Uprising`` -> ``Kid Icarus - Uprising (USA).png``)
    or a region/language suffix still matches the bare Checkpoint title.

    When several entries match, they are accepted only when they are all
    region/language variants of **one** normalised title; the USA release is
    then preferred.  Two genuinely different titles (a shared fragment such as
    ``Super``) stay ambiguous and return ``None``.
    """
    return _resolve_unique_match(_matching_entries(filenames, query))


def ambiguous_boxart_matches(
    filenames: Sequence[str], query: Any
) -> Tuple[str, ...]:
    """Listing entries the deterministic matcher cannot reduce to one pick.

    Returns the matching file names only when at least two are present *and*
    :func:`unique_boxart_match` cannot resolve them -- i.e. genuinely different
    titles sharing a query.  Region/language variants of one title (already
    resolved to USA) and unique/no-match queries both return ``()``, so the
    optional LLM is consulted only for a real ambiguity.
    """
    matches = _matching_entries(filenames, query)
    if len(matches) < 2:
        return ()
    if _resolve_unique_match(matches) is not None:
        return ()
    return tuple(matches)


# Generic function words that carry no box-art signal.  Dropping them before the
# loose comparison stops ``The Legend of Zelda`` from matching every file that
# merely contains "the"/"of".
_LOOSE_STOPWORDS: FrozenSet[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "of",
        "or",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "as",
        "is",
        "it",
        "its",
        "no",
        "vs",
    }
)

# Never offer the optional LLM more than this many loose candidates, so one
# listing page cannot grow the prompt without bound.
MAX_LOOSE_CANDIDATES = 40


def _content_tokens(value: Any) -> Tuple[str, ...]:
    """Tokens of ``value`` with stopwords and parenthetical glosses removed.

    Dropping ``(...)`` groups lets a query like ``Monster Hunter 3 (Try) G``
    line up its ``3`` and ``G`` words with the concatenated listing token
    ``3G`` -- a bracketed pronunciation gloss or region tag must not split a
    word that the real file name joins together.
    """
    text = _PAREN_RE.sub(" ", str(value or ""))
    return tuple(token for token in _tokens(text) if token not in _LOOSE_STOPWORDS)


def _joined_run_count(parts: Tuple[str, ...], wholes: FrozenSet[str]) -> int:
    """How many adjacent runs in ``parts`` join into a token of ``wholes``.

    Only runs of **two or more** tokens count, so a lone shared word is never
    double-scored. ``(heart, gold)`` -> ``heartgold`` and ``(3, g)`` -> ``3g``
    are the concatenation variants libretro filenames use.
    """
    found = 0
    seen = set()
    for start in range(len(parts)):
        joined = parts[start]
        for end in range(start + 1, len(parts)):
            joined += parts[end]
            if len(joined) < 2 or joined not in wholes or joined in seen:
                continue
            seen.add(joined)
            found += 1
    return found


def _concatenation_matches(
    query_tokens: Tuple[str, ...], entry_tokens: Tuple[str, ...]
) -> int:
    """Count words one side writes as a single token and the other splits.

    Catches ``Heart Gold`` vs ``HeartGold`` and ``3 G`` vs ``3G`` in either
    direction, so a concatenated listing word is ranked ahead of a neighbour
    that merely shares words with the query.
    """
    return _joined_run_count(query_tokens, frozenset(entry_tokens)) + _joined_run_count(
        entry_tokens, frozenset(query_tokens)
    )


def loose_boxart_candidates(
    filenames: Sequence[str],
    query: Any,
    *,
    limit: int = MAX_LOOSE_CANDIDATES,
) -> Tuple[str, ...]:
    """A scored, capped word-overlap pool for the optional LLM.

    Unlike :func:`unique_boxart_match`, a candidate only needs **one** shared
    content word (stopwords and parenthetical glosses ignored), which catches
    real filenames the strict token matcher cannot line up (``Pokemon Heart
    Gold`` -> ``Pokemon - HeartGold Version (USA).png``).  A listing word that
    merely concatenates the query's words (``HeartGold``, ``3G``) is ranked
    ahead of a neighbour that shares more but separate words, and the result is
    capped so a listing page cannot produce an unbounded prompt.
    """
    query_tokens = _content_tokens(query)
    query_set = set(query_tokens)
    if not query_set:
        return ()
    scored = []
    for name in filenames:
        if not name:
            continue
        entry_tokens = _content_tokens(_strip_png(str(name)))
        if not entry_tokens:
            continue
        overlap = len(query_set.intersection(entry_tokens))
        if not overlap:
            continue
        concatenated = _concatenation_matches(query_tokens, entry_tokens)
        scored.append((concatenated, overlap, _region_rank(name), str(name)))
    if not scored:
        return ()
    # A concatenated word is the strongest signal, then raw shared-word count;
    # region preference only breaks ties.
    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    capped = scored[: max(int(limit), 0)]
    return tuple(name for _, _, _, name in capped)


def concatenation_boxart_candidates(
    filenames: Sequence[str],
    query: Any,
    *,
    limit: int = MAX_LOOSE_CANDIDATES,
) -> Tuple[str, ...]:
    """Entries whose words a query's tokens join into one token.

    A real file name that concatenates the query's words (``3 G`` -> ``3G``,
    ``Heart Gold`` -> ``HeartGold``) is a much stronger signal than the token
    containment :func:`unique_boxart_match` uses, where a longer title that
    merely repeats the query words (``Monster Hunter 3`` -> ``Monster Hunter 3
    Ultimate``) can look like the answer.  Callers offer this precise pool to
    the optional LLM *before* the strict ambiguous pool, so a genuine
    concatenated title is never preempted by such a neighbour.  Empty when no
    entry concatenates the query, leaving the strict rule in charge.
    """
    query_tokens = _content_tokens(query)
    if not query_tokens:
        return ()
    scored = []
    for name in filenames:
        if not name:
            continue
        entry_tokens = _content_tokens(_strip_png(str(name)))
        if not entry_tokens:
            continue
        concatenated = _concatenation_matches(query_tokens, entry_tokens)
        if concatenated <= 0:
            continue
        scored.append((concatenated, _region_rank(name), str(name)))
    if not scored:
        return ()
    # Most joined words first; region preference only breaks a tie.
    scored.sort(key=lambda item: (-item[0], item[1]))
    capped = scored[: max(int(limit), 0)]
    return tuple(name for _, _, name in capped)


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


@lru_cache(maxsize=1)
def _known_nds_serials() -> FrozenSet[str]:
    """The 4-character NDS cartridge serials from the bundled offline index."""
    path = bundled_libretro_dir() / _NDS_SERIAL_INDEX_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return frozenset()
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list):
        return frozenset()
    serials = set()
    for record in records:
        if isinstance(record, (list, tuple)) and len(record) > 3:
            code = str(record[3] or "").strip().upper()
            if len(code) == 4:
                serials.add(code)
    return frozenset(serials)


def _is_ds_cartridge_code(code: str) -> bool:
    """Whether a 4-character prefix is a real DS cartridge code.

    The shipped NDS serial index is authoritative (so a title word like ``NANO``
    is never a code, while an NDSi serial ending in ``O`` such as ``IRBO`` is).
    Without the index a region/language marker on the fourth character is the
    fallback.
    """
    serials = _known_nds_serials()
    if serials:
        return code in serials
    return code[-1:] in _DS_REGION_MARKERS


def strip_ds_cartridge_code(name: Any) -> Optional[str]:
    """Return the title in ``AZEJ Kirby Super Star Ultra``, or ``None``.

    Only an all-uppercase 4-character game code counts, so a normal mixed-case
    3DS title (``Cube Creator 3D``) is never mistaken for a cartridge code.
    The code must also be a **real** DS serial from the shipped NDS index (or,
    without that index, carry a region/language marker), so a bare title word
    (``NANO`` in "Nano Assault") is rejected.
    """
    match = _DS_CARTRIDGE_RE.match(str(name or "").strip())
    if not match:
        return None
    code = match.group(1)
    if not _is_ds_cartridge_code(code):
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
    "MAX_LOOSE_CANDIDATES",
    "ambiguous_boxart_matches",
    "concatenation_boxart_candidates",
    "fetch_boxart_listing",
    "loose_boxart_candidates",
    "normalize_boxart_name",
    "parse_boxart_listing",
    "resolve_boxart_system",
    "strip_ds_cartridge_code",
    "unique_boxart_match",
]
