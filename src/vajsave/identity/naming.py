"""Filename normalisation helpers shared by the identity resolvers.

ROM collections use wildly inconsistent naming ("Pokemon Emerald (USA) (Rev 1)
[!].gba").  A save is matched to a ROM by reducing both names to a stable,
region-free form while remembering the region tag for display.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional

_PAREN_RE = re.compile(r"[\(\[]([^\)\]]*)[\)\]]")
_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")
_NON_ALNUM_RE = re.compile(r"[^0-9a-z]+")

# Canonical region names keyed by every common abbreviation found in ROM names.
_REGION_ALIASES = {
    "usa": "USA",
    "us": "USA",
    "u": "USA",
    "europe": "Europe",
    "eur": "Europe",
    "eu": "Europe",
    "e": "Europe",
    "japan": "Japan",
    "jp": "Japan",
    "jpn": "Japan",
    "j": "Japan",
    "world": "World",
    "w": "World",
    "australia": "Australia",
    "aus": "Australia",
    "korea": "Korea",
    "kor": "Korea",
    "china": "China",
    "chn": "China",
    "taiwan": "Taiwan",
    "twn": "Taiwan",
    "canada": "Canada",
    "can": "Canada",
    "france": "France",
    "fra": "France",
    "fre": "France",
    "germany": "Germany",
    "ger": "Germany",
    "deu": "Germany",
    "spain": "Spain",
    "spa": "Spain",
    "italy": "Italy",
    "ita": "Italy",
    "netherlands": "Netherlands",
    "ned": "Netherlands",
    "nld": "Netherlands",
    "sweden": "Sweden",
    "swe": "Sweden",
    "russia": "Russia",
    "rus": "Russia",
    "brazil": "Brazil",
    "bra": "Brazil",
    "asia": "Asia",
    "uk": "UK",
    "england": "UK",
}


def strip_extension(name: str) -> str:
    """Return the file name without a short, plausible extension."""
    base = Path(str(name)).name
    match = _EXT_RE.search(base)
    if match:
        base = base[: match.start()]
    return base or str(name)


def _region_tokens(name: str) -> List[str]:
    tokens: List[str] = []
    for match in _PAREN_RE.finditer(str(name)):
        content = match.group(1)
        for part in re.split(r"[,\s]+", content):
            if part:
                tokens.append(part)
    return tokens


def extract_region(name: str) -> Optional[str]:
    """Return the canonical region tag from a ROM/save name, if any."""
    for token in _region_tokens(name):
        canonical = _REGION_ALIASES.get(token.strip().lower())
        if canonical:
            return canonical
    return None


def normalize_title(name: str) -> str:
    """Reduce ``name`` to a lowercase, punctuation- and region-free match key."""
    base = strip_extension(name)
    base = _PAREN_RE.sub(" ", base)
    base = base.lower()
    base = _NON_ALNUM_RE.sub(" ", base)
    return " ".join(base.split())


def display_name_from_stem(name: str) -> str:
    """Human-readable title from a file name (drops bracket tags, keeps words)."""
    base = strip_extension(name)
    base = _PAREN_RE.sub(" ", base)
    cleaned = re.sub(r"\s+", " ", base.replace("_", " ")).strip(" -_.")
    return cleaned or strip_extension(name)


def title_tokens(name: str) -> frozenset:
    """Normalised, region-free token set used for conservative fuzzy matching."""
    return frozenset(normalize_title(name).split())


def conservative_token_match(hint_tokens: frozenset, candidate_tokens: frozenset) -> bool:
    """True when every token of ``hint_tokens`` also appears in ``candidate_tokens``.

    Stage-2 matching is deliberately one-directional: the save name is a
    *hint* ("Pokemon Emerald") while the ROM name may carry extra words
    ("Pokemon Emerald Version").  Requiring the hint's tokens to be a non-empty
    subset of the ROM's tokens avoids the false positive of matching a longer
    save name against a shorter, coarser ROM name (“Pokemon Emerald” vs
    “Pokemon”).
    """
    return bool(hint_tokens) and hint_tokens <= candidate_tokens


def save_hint(entry) -> str:
    """Best available name for a save entry, independent of its absolute path.

    Prefers the scanned ``display_name``; falls back to the basename of the save
    path so a moved save directory keeps producing the same hint.
    """
    name = (getattr(entry, "display_name", "") or "").strip()
    if name:
        return name
    raw = getattr(entry, "path", "") or ""
    return strip_extension(raw)


def iter_region_tokens(name: str) -> Iterable[str]:
    """Expose the raw bracketed tokens; kept for callers building custom logic."""
    return tuple(_region_tokens(name))
