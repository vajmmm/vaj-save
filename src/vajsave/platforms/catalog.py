"""Platform dock order and display labels."""

HANDHELD_PLATFORMS = ["psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"]
CONSOLE_PLATFORMS = ["ps3", "ps4", "wiiu", "wii", "x360"]
ALL_PLATFORMS = HANDHELD_PLATFORMS + CONSOLE_PLATFORMS

HANDHELD_ORDER = ["all", *HANDHELD_PLATFORMS]
CONSOLE_ORDER = ["all", *CONSOLE_PLATFORMS]

PLATFORM_ORDER = ["all", *HANDHELD_PLATFORMS, *CONSOLE_PLATFORMS]

PLATFORM_LABELS = {
    "all": "全部",
    "psp": "PSP",
    "vita": "PS Vita",
    "switch": "Switch",
    "3ds": "3DS",
    "nds": "NDS",
    "gb": "GB",
    "gbc": "GBC",
    "gba": "GBA",
    "ps3": "PS3",
    "ps4": "PS4",
    "wiiu": "Wii U",
    "wii": "Wii",
    "x360": "Xbox 360",
}

PLATFORM_CATEGORIES = {
    "handheld": HANDHELD_PLATFORMS,
    "console": CONSOLE_PLATFORMS,
}
