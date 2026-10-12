"""Platform dock order and display labels."""

HANDHELD_PLATFORMS = ["psp", "vita", "switch", "3ds", "nds", "gb", "gbc", "gba"]
ALL_PLATFORMS = list(HANDHELD_PLATFORMS)

HANDHELD_ORDER = ["all", *HANDHELD_PLATFORMS]

PLATFORM_ORDER = ["all", *HANDHELD_PLATFORMS]

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
}

PLATFORM_CATEGORIES = {
    "handheld": HANDHELD_PLATFORMS,
}
