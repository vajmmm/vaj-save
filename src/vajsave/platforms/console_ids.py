"""Title-id cleanup shared by the home-console scanners, identity and covers.

The scanners meet several spellings of the same id (SFO product code, an
8-digit Wii NAND folder, a Wii U save folder that is only the low half of the
title id). Keeping one normalizer stops the gallery from splitting one game
into two cards and gives the cover lookup a stable key.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_PS3_ID = re.compile(r"([A-Z]{4}\d{5})", re.IGNORECASE)
_PS4_ID = re.compile(r"(CUSA\d{5})", re.IGNORECASE)
_WUP_CODE = re.compile(r"WUP-[A-Z0-9]-([A-Z0-9]{4})", re.IGNORECASE)
_ALNUM4 = re.compile(r"^[A-Z0-9]{4}$")
_ALNUM6 = re.compile(r"^[A-Z0-9]{6}$")
_HEX8 = re.compile(r"^[0-9A-F]{8}$")
_HEX16 = re.compile(r"^[0-9A-F]{16}$")


def _upper(value: object) -> str:
    return str(value or "").strip().upper()


def normalize_ps3_title_id(value: object) -> Optional[str]:
    match = _PS3_ID.search(_upper(value))
    return match.group(1).upper() if match else None


def normalize_ps4_title_id(value: object) -> Optional[str]:
    match = _PS4_ID.search(_upper(value))
    return match.group(1).upper() if match else None


def normalize_wii_title_id(value: object) -> Optional[str]:
    """Return a 4- or 6-character Wii game id.

    NAND folders are 8 hex digits of the 4-character ASCII code (``524D4345``
    is ``RMCE``). SaveGame Manager GX already uses the 6-character id.
    """
    text = _upper(value)
    if _HEX8.fullmatch(text):
        try:
            decoded = bytes.fromhex(text).decode("ascii")
        except (ValueError, UnicodeDecodeError):
            decoded = ""
        if _ALNUM4.fullmatch(decoded.upper()):
            return decoded.upper()
    compact = re.sub(r"[^A-Z0-9]", "", text)
    if _ALNUM4.fullmatch(compact) or _ALNUM6.fullmatch(compact):
        return compact
    return None


def normalize_wiiu_title_id(value: object) -> Optional[str]:
    """Prefer a product code, else a 16-digit title id.

    An 8-digit save folder is the low half of a retail title (``00050000``).
    """
    raw = _upper(value)
    match = _WUP_CODE.search(raw)
    if match:
        return match.group(0).upper()
    compact = re.sub(r"[^0-9A-F]", "", raw)
    if _HEX16.fullmatch(compact):
        return compact
    if _HEX8.fullmatch(compact):
        return "00050000" + compact
    return None


def normalize_x360_title_id(value: object) -> Optional[str]:
    compact = re.sub(r"[^0-9A-F]", "", _upper(value))
    if _HEX8.fullmatch(compact):
        return compact
    return None


def wii_cover_ids(value: object) -> Tuple[str, ...]:
    """GameTDB ids for a Wii code. A 4-character NAND id grows a ``01`` maker."""
    code = normalize_wii_title_id(value)
    if not code:
        return ()
    if _ALNUM6.fullmatch(code):
        return (code,)
    if _ALNUM4.fullmatch(code):
        return (code + "01",)
    return ()


def wiiu_cover_ids(value: object) -> Tuple[str, ...]:
    """GameTDB ids for a Wii U product code.

    Retail covers are usually ``<4-char>01`` (``ALZE01``). A few are filed
    under the bare 4-character code (``AL5E``), so both are offered, longer
    first.
    """
    text = _upper(value)
    match = _WUP_CODE.search(text)
    code = match.group(1).upper() if match else ""
    if not code:
        compact = re.sub(r"[^A-Z0-9]", "", text)
        if _ALNUM6.fullmatch(compact) and re.search(r"[A-Z]", compact):
            return (compact,)
        if _ALNUM4.fullmatch(compact) and re.search(r"[A-Z]", compact):
            code = compact
    if not code:
        return ()
    longer = code + "01"
    if longer == code:
        return (code,)
    return (longer, code)
