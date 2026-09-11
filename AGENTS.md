# Development Rules (vaj-save)

## Environment & Testing
- Python 3.11+ environment.
- Run tests using `pytest`:
  ```bash
  pytest
  ```
- Do not hardcode OS-specific drive letters or paths; ensure cross-platform compatibility across Windows, macOS, and Linux.

## UI & Visual Design
- Whenever modifying or creating desktop UI components, views, dialogs, or colors, strictly adhere to [DESIGN.md](./DESIGN.md).
- Follow the Switch Basic White palette defined in [DESIGN.md](./DESIGN.md) (`#ebebeb` / `#f2f2f2` / `#e7e7e7` / `#ffffff` / `#2d2d2d`, accent `#0a84ff`).
- Reference the tokens in `vajsave.ui_theme` instead of hardcoding colour values.
- Use the canonical platform colors (`PLATFORM_COLORS`) for the 3px pips only.
- Keep the middle column a quiet single-column（单列）list of flat save rows; no card grids, grow-on-select, glow rings, stars, platform bars or monograms.
- Keep the right panel an inspector: a 3-button header (备份 / 恢复 / 导出 ZIP), a definition list, a versions list that fills the remaining height, and a note entry.
- Preserve the 3-column layout structure. The only page-level accent fill is the blue 备份 button.
