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
- Use the canonical platform colors (`PLATFORM_COLORS`) for PSP, PS Vita, Switch, 3DS, NDS, and GBA.
- Preserve the 3-column layout structure and keep right-panel action buttons pinned to the bottom.
