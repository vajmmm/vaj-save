"""Identification and filtering of homebrew utilities, system tools, and non-game apps.

Homebrew tools (such as VitaShell, Adrenaline Bubbles Manager, MaiDumpTool, DBI,
JKSV, Checkpoint, FBI, GodMode9, etc.) are utilities rather than commercial or
retail games. They do not contain playable game saves that need archiving or
management. This module provides centralized detection and filtering across
all supported platforms (PS Vita, PSP, 3DS, Switch, NDS, etc.).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, List, Optional, Set

logger = logging.getLogger("vajsave.homebrew")

# PS Vita known homebrew / tool Title IDs (case-insensitive)
VITA_HOMEBREW_TITLE_IDS: Set[str] = {
    # System & File Managers
    "VITASHELL",
    "MOLECULAR",
    "PSP2SHELL",
    "SKGD3PL0Y",  # VitaDeploy
    "SAVEMGR00",  # Vita Save Manager
    # Custom Firmware & Exploits
    "HENKAKU00",
    "HENLO0000",
    "ENSO00000",
    "ITLS00000",
    "MODORU000",  # Modoru
    # Package & Game Installers / Dumpers
    "MAIM00001",  # MaiDumpTool
    "MAIDUMP00",
    "PKGI00000",  # PKGi / PKGj
    "PKGJP0000",
    "FDM000001",  # Package Installer
    # Plugins & System Utilities
    "ADRBUBMAN",  # Adrenaline Bubbles Manager
    "ADRBUBM00",
    "ADRENALIN",  # Adrenaline
    "AUTOPLUG0",  # AutoPlugin
    "AUTOPLUG2",  # AutoPlugin II
    "TOOLBOX00",  # VITA工具箱
    "TOOLBOX20",  # TOOLBOX2
    "CUSTOMTHM",  # Custom Themes Manager
    "BATTERYF0",  # BatteryFixer
    "VITAIDENT",  # VitaIdent
    "YAMT00000",  # YAMT
    "TFPRECONF",  # TF Card Plugin Tool
    "DSPRECONF",  # DualShock Plugin Tool
    "UDCDUVC00",  # UDCD-UVC
    "SHARKF00D",  # SharkF00D
    "BETTERTRM",  # BetterTrackManager
    "VITAOCTRA",  # Vita Nearest Neighbor
    "VITACOMPR",  # VitaCompress
    "OVERCLOC0",  # Overclock tools
    "LOLIC0N00",
    "PSVITAOVL",
    "VITARES00",  # VitaRes
    "PSVUPDAT0",  # Update Blocker
    "REGISTRY0",  # Registry Editor
    "VPKBUILD0",
    "CBM000001",  # Custom Boot Splash
    # Media & Streaming clients
    "DUSKGXM01",  # dusklight Vita / Moonlight frontend
    "WILIWILI0",  # wiliwili (Bilibili client)
    "WILIWILI",
    "MOONLIGHT",
    "VPLAY0000",
    "NOBD00001",
}

# PSP known homebrew / tool IDs or folder names in SAVEDATA
PSP_HOMEBREW_IDS: Set[str] = {
    "FASTRECOVERY",
    "FASTRECOV",
    "FAST_RECOVERY",
    "PROUPDATE",
    "PRO_UPDATE",
    "MEUPDATE",
    "LMEUPDATE",
    "CIDENT",
    "PSPIDENT",
    "CWCHEAT",
    "PRXLOADER",
    "RECOVERY",
    "ARK_01234",
    "ARK4",
    "ARK_DC",
    "PSPFILER",
    "FILER",
    "CHRONOSWITCH",
    "KEYCLEANER",
    "PANDORA",
    "TIME_MACHINE",
}

# 3DS known homebrew / tool Title IDs or Checkpoint folders
THREEDS_HOMEBREW_IDS: Set[str] = {
    "00040000000F8000",  # FBI
    "0X00F80",
    "0004000000021400",  # Homebrew Launcher
    "0X00214",
    "0004000000177700",  # Checkpoint
    "0X01777",
    "0004000000167800",
    "0X01678",
    "0004000000167900",
    "0X01679",
    "0004000000190100",  # Anemone3DS
    "0X01901",
    "0004000000188800",  # Universal-Updater
    "0X01888",
    "00040000000EDF00",  # Luma3DS Updater
    "0X00EDF",
    "000400000011F000",  # FTPD
    "0X011F0",
    "0004000000155500",  # GodMode9
    "0X01555",
    "0004000000133700",  # JKSM
    "0X01337",
    "00040000000B3300",  # TWiLight Menu++
    "0X00B33",
    "0004000000133800",  # PKSM
    "0X01338",
    "0004000000055F00",  # DSP1
    "0X0055F",
    "0004000000188900",  # 3DSident
    "0X01889",
}

# Switch known homebrew / tool Title IDs
SWITCH_HOMEBREW_IDS: Set[str] = {
    "010000000000100D",  # Homebrew Menu
    "0500000000000000",  # JKSV
    "054E464558540000",  # DBI
    "0500000000000001",  # Checkpoint
    "0500000000000002",  # Tinfoil
    "0500000000000003",  # Goldleaf
    "0500000000000004",  # Awoo Installer
    "0500000000000005",  # Daybreak
    "0500000000000006",  # EdiZon
    "0500000000000007",  # NX-Shell
    "0500000000000008",  # MissionControl
    "0500000000000009",  # TegraExplorer
    "050000000000000A",  # PayloadLauncher
    "050000000000000B",  # RetroArch
    "05B9005401000000",  # Tinwoo
    "050000000000000E",  # AtmoPackUpdater
    "0500000000000010",  # Breeze
    "0500000000000012",  # AIO-Switch-Updater
    "0500000000000014",  # NX-Activity-Log
    "0100000000001000",  # qlaunch (system)
}

# Global name keywords matching homebrew utilities / tools across all platforms
HOMEBREW_NAME_PATTERNS = [
    re.compile(r"adrenaline\s*bubbles\s*manager", re.IGNORECASE),
    re.compile(r"maidumptool", re.IGNORECASE),
    re.compile(r"vitashell", re.IGNORECASE),
    re.compile(r"molecularshell", re.IGNORECASE),
    re.compile(r"autoplugin", re.IGNORECASE),
    re.compile(r"vitadeploy", re.IGNORECASE),
    re.compile(r"vita\s*save\s*manager", re.IGNORECASE),
    re.compile(r"custom\s*themes\s*manager", re.IGNORECASE),
    re.compile(r"batteryfixer", re.IGNORECASE),
    re.compile(r"vitaident|3dsident|pspident", re.IGNORECASE),
    re.compile(r"\bmodoru\b", re.IGNORECASE),
    re.compile(r"itls-enso|\benso\b", re.IGNORECASE),
    re.compile(r"vita工具箱|toolbox", re.IGNORECASE),
    re.compile(r"dusklight\s*vita", re.IGNORECASE),
    re.compile(r"\bwiliwili\b", re.IGNORECASE),
    re.compile(r"\bpkgj\b|\bpkgi\b", re.IGNORECASE),
    re.compile(r"\bhenkaku\b|\btaihen\b", re.IGNORECASE),
    re.compile(r"fast\s*recovery", re.IGNORECASE),
    re.compile(r"pro\s*update|lme\s*update|me\s*update", re.IGNORECASE),
    re.compile(r"\bark-4\b|\bark4\b", re.IGNORECASE),
    re.compile(r"\bcwcheat\b", re.IGNORECASE),
    re.compile(r"psp\s*filer|\bpspfiler\b", re.IGNORECASE),
    re.compile(r"^\s*fbi\b|\bfbi\s*$", re.IGNORECASE),
    re.compile(r"^\s*jksv\b|\bjksv\s*$|^\s*jksm\b|\bjksm\s*$", re.IGNORECASE),
    re.compile(r"^\s*checkpoint\s*$", re.IGNORECASE),  # matched only when game title itself is "Checkpoint"
    re.compile(r"homebrew\s*launcher", re.IGNORECASE),
    re.compile(r"anemone3ds|anemone\s*3ds", re.IGNORECASE),
    re.compile(r"universal-updater|universal\s*updater", re.IGNORECASE),
    re.compile(r"luma3ds|luma\s*updater", re.IGNORECASE),
    re.compile(r"godmode9|\bgm9\b", re.IGNORECASE),
    re.compile(r"^\s*ftpd\s*$", re.IGNORECASE),
    re.compile(r"twilight\s*menu|twilightmenu", re.IGNORECASE),
    re.compile(r"^\s*pksm\s*$", re.IGNORECASE),
    re.compile(r"^\s*dbi\b|\bdbi\s*installer\b", re.IGNORECASE),
    re.compile(r"^\s*tinfoil\s*$", re.IGNORECASE),
    re.compile(r"^\s*goldleaf\s*$", re.IGNORECASE),
    re.compile(r"awoo\s*installer|\btinwoo\b", re.IGNORECASE),
    re.compile(r"^\s*daybreak\s*$", re.IGNORECASE),
    re.compile(r"^\s*edizon\s*$", re.IGNORECASE),
    re.compile(r"nx-shell|nxshell", re.IGNORECASE),
    re.compile(r"hbmenu|homebrew\s*menu", re.IGNORECASE),
    re.compile(r"payloadlauncher|tegraexplorer", re.IGNORECASE),
]

FLASH_CART_SYSTEM_NAMES: Set[str] = {
    "_rpg",
    "_system_",
    "moonshl",
    "moonshl2",
    "ysmenu",
    "wood_r4",
    "ttmenu",
}


def is_homebrew_or_tool(
    entry_or_platform: Any,
    title_id: Optional[str] = None,
    display_name: Optional[str] = None,
    path: Optional[str] = None,
) -> bool:
    """Determine whether an entry represents a homebrew utility or system tool."""
    if isinstance(entry_or_platform, str):
        plat = entry_or_platform.strip().lower()
        tid = str(title_id or "").strip().upper()
        name = str(display_name or "").strip()
        loc = str(path or "").strip()
    else:
        entry = entry_or_platform
        plat = str(getattr(entry, "platform", "") or "").strip().lower()
        tid = str(getattr(entry, "title_id", "") or "").strip().upper()
        name = str(getattr(entry, "display_name", "") or "").strip()
        loc = str(getattr(entry, "path", "") or "").strip()

    # 1. Check Title ID / Product Code by platform
    if tid:
        cleaned_tid = tid.replace("0X", "").replace("-", "").upper()
        if plat == "vita":
            if tid in VITA_HOMEBREW_TITLE_IDS or cleaned_tid in VITA_HOMEBREW_TITLE_IDS:
                return True
        elif plat == "psp":
            if tid in PSP_HOMEBREW_IDS or cleaned_tid in PSP_HOMEBREW_IDS:
                return True
        elif plat in ("3ds", "threeds"):
            if tid in THREEDS_HOMEBREW_IDS or cleaned_tid in THREEDS_HOMEBREW_IDS:
                return True
        elif plat == "switch":
            if tid in SWITCH_HOMEBREW_IDS or cleaned_tid in SWITCH_HOMEBREW_IDS:
                return True

    # 2. Check path leaf / folder name
    if loc:
        leaf = Path(loc).name
        leaf_upper = leaf.upper()
        leaf_lower = leaf.lower()
        if leaf_lower in FLASH_CART_SYSTEM_NAMES:
            return True
        if plat == "vita" and leaf_upper in VITA_HOMEBREW_TITLE_IDS:
            return True
        if plat == "psp" and leaf_upper in PSP_HOMEBREW_IDS:
            return True

    # 3. Check display name against keyword patterns
    if name:
        for pattern in HOMEBREW_NAME_PATTERNS:
            if pattern.search(name):
                return True

    # 4. Check folder leaf name against keyword patterns
    if loc:
        leaf = Path(loc).name
        for pattern in HOMEBREW_NAME_PATTERNS:
            if pattern.search(leaf):
                return True

    return False


def filter_homebrew_saves(saves: Iterable[Any]) -> List[Any]:
    """Filter out homebrew and system utility entries from discovered saves."""
    filtered: List[Any] = []
    for entry in saves:
        if is_homebrew_or_tool(entry):
            plat = getattr(entry, "platform", "unknown")
            name = getattr(entry, "display_name", "") or getattr(entry, "path", "")
            tid = getattr(entry, "title_id", "") or "无"
            logger.info("过滤自制软件/系统工具: [%s] '%s' (Title ID: %s)", plat, name, tid)
            continue
        filtered.append(entry)
    return filtered
