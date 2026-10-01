"""Group multiple save entries into single game entries across all platforms."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, List, Optional

from .identity.naming import normalize_title
from .models import SaveEntry

if TYPE_CHECKING:
    from .app_state import AppState


def game_identity_key_for_entry(entry: SaveEntry, app: Optional[AppState] = None) -> str:
    """Determine the canonical game identity key for a SaveEntry.

    Used to group multiple physical save directories/slots/sources of the same
    game across any platform (Switch, 3DS, PSP, Vita, GBA, NDS, GB, GBC) into a
    single game card in the gallery.
    """
    platform = (getattr(entry, "platform", None) or "unknown").strip().lower()

    # If the entry already carries an identity key (e.g. from local catalog)
    extra = getattr(entry, "extra", None) or {}
    if extra.get("identity_key"):
        return str(extra["identity_key"]).strip()

    # Use the app's GameIdentityResolver if available
    if app is not None:
        try:
            res = app.resolve_save_identity(entry)
            if res and res.identity and res.identity.identity_key:
                return res.identity.identity_key
        except Exception:
            pass

    # Platform-specific fallbacks if resolution didn't produce an identity_key
    raw_tid = (getattr(entry, "title_id", None) or "").strip()
    display = (getattr(entry, "display_name", None) or "").strip()

    if platform == "switch":
        if raw_tid:
            from .platforms.switch import _clean_switch_title_id
            clean_tid = _clean_switch_title_id(raw_tid)
            if clean_tid:
                return f"switch:{clean_tid}"
        if display:
            from .artwork.switch_covers import get_switch_id_for_title
            matched = get_switch_id_for_title(display)
            if matched:
                return f"switch:{matched}"
            return f"switch:name:{normalize_title(display)}"

    elif platform == "psp":
        if raw_tid:
            # SFO title ids on PSP are typically 9 chars (e.g. ULJM05800)
            tid_core = raw_tid
            if len(raw_tid) > 9 and raw_tid[:4].isalpha():
                tid_core = raw_tid[:9]
            return f"psp:{tid_core.upper()}"
        if display:
            return f"psp:name:{normalize_title(display)}"

    elif platform in ("3ds", "vita"):
        if raw_tid:
            return f"{platform}:{raw_tid.upper()}"
        if display:
            return f"{platform:name}:{normalize_title(display)}"

    # Universal name-based grouping fallback
    name = display or Path(entry.path).stem
    return f"{platform}:name:{normalize_title(name)}"


def format_sub_entry_label(entry: SaveEntry) -> str:
    """User-friendly label for a specific save slot/source in the DetailDrawer."""
    parts = []
    source = (getattr(entry, "source_id", None) or "").lower()
    if "dbi" in source:
        parts.append("DBI")
    elif "jksv" in source or "jksm" in source:
        parts.append("JKSV")
    elif "checkpoint" in source:
        parts.append("Checkpoint")
    elif "savedata" in source or getattr(entry, "platform", "") == "psp":
        parts.append("SAVEDATA")
    elif "export" in source:
        parts.append("导出备份")
    else:
        parts.append(getattr(entry, "source_id", "默认") or "默认")

    user = getattr(entry, "user", None)
    if user:
        parts.append(f"用户: {user}")
    slot = getattr(entry, "slot", None)
    if slot:
        parts.append(f"槽位: {slot}")

    if len(parts) == 1:
        p = Path(entry.path)
        parts.append(p.name)

    return " · ".join(parts)


def group_saves_by_game(
    entries: Iterable[SaveEntry], app: Optional[AppState] = None
) -> List[SaveEntry]:
    """Group save entries by game identity, returning one representative entry per game.

    Each representative entry stores all its physical saves in
    ``entry.extra['sub_entries']``.
    """
    entries_list = list(entries)
    if not entries_list:
        return []

    groups: OrderedDict[str, List[SaveEntry]] = OrderedDict()
    for entry in entries_list:
        # If the entry is already a game group, expand its sub_entries
        subs = (entry.extra or {}).get("sub_entries")
        if subs and len(subs) > 0:
            for sub in subs:
                key = game_identity_key_for_entry(sub, app)
                groups.setdefault(key, []).append(sub)
        else:
            key = game_identity_key_for_entry(entry, app)
            groups.setdefault(key, []).append(entry)

    grouped_result: List[SaveEntry] = []
    for key, group in groups.items():
        if len(group) == 1:
            single = group[0]
            extra = dict(single.extra or {})
            extra["sub_entries"] = [single]
            extra["group_key"] = key
            # Update extra without mutating caller's original dict
            rep = SaveEntry(
                platform=single.platform,
                source_id=single.source_id,
                display_name=single.display_name,
                path=single.path,
                title_id=single.title_id,
                slot=single.slot,
                user=single.user,
                extra=extra,
                cover_path=single.cover_path,
            )
            grouped_result.append(rep)
        else:
            # Multiple saves for the same game!
            # Pick primary entry (prefer changed/new, then newest file mtime)
            primary = _pick_primary_entry(group, app)

            # Choose best display title (avoid raw hex Title ID if better title exists)
            best_name = _pick_best_display_name(group, key, app)

            # Best Title ID
            best_tid = next((e.title_id for e in group if e.title_id), primary.title_id)
            if not best_tid and ":" in key:
                parts = key.split(":", 1)
                if len(parts) == 2 and not parts[1].startswith("name:"):
                    best_tid = parts[1]

            # Best cover path
            best_cover = next((e.cover_path for e in group if e.cover_path), primary.cover_path)

            extra = dict(primary.extra or {})
            extra["sub_entries"] = list(group)
            extra["group_key"] = key
            extra["is_game_group"] = True

            rep = SaveEntry(
                platform=primary.platform,
                source_id=primary.source_id,
                display_name=best_name,
                path=primary.path,
                title_id=best_tid,
                slot=primary.slot,
                user=primary.user,
                extra=extra,
                cover_path=best_cover,
            )
            grouped_result.append(rep)

    return grouped_result


def _pick_primary_entry(group: List[SaveEntry], app: Optional[AppState]) -> SaveEntry:
    if app is not None:
        # Prefer an entry that has updates (changed or new)
        for entry in group:
            try:
                status = app.save_status(entry)
                if status.status in ("changed", "new"):
                    return entry
            except Exception:
                pass

    # Fallback to newest mtime
    def _mtime(entry: SaveEntry) -> float:
        try:
            return Path(entry.path).stat().st_mtime
        except OSError:
            return 0.0

    return max(group, key=_mtime)


def _pick_best_display_name(
    group: List[SaveEntry], group_key: str, app: Optional[AppState]
) -> str:
    from .platforms.switch import _clean_switch_title_id

    # If app can resolve identity with canonical title
    if app is not None:
        try:
            res = app.resolve_save_identity(group[0])
            if res and res.identity and res.identity.title:
                t = res.identity.title.strip()
                if not _clean_switch_title_id(t) and not t.startswith("0x"):
                    return t
        except Exception:
            pass

    # Filter out entries whose display_name is just hex or empty
    candidates = []
    for entry in group:
        name = (entry.display_name or "").strip()
        if name and not _clean_switch_title_id(name) and not (name.startswith("0x") and len(name.split()) <= 2):
            candidates.append(name)

    if candidates:
        # Longest / cleanest candidate
        return max(candidates, key=len)

    return group[0].display_name or Path(group[0].path).name
