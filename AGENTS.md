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
- Use the canonical platform colors (`PLATFORM_COLORS`) for the 3px gallery pips only; keep all other surfaces neutral and token-owned.
- Keep the middle column as a responsive（响应式）游戏画廊（gallery） of physical cartridge/box artwork on quiet silver display shelves（陈列架）. Preserve multi-selection, keyboard navigation and cover caching; selected cases may use a restrained blue focus outline.
- Keep the right panel as a fog-white detail drawer（详情抽屉） that slides over the gallery after selection. It contains the three core actions (备份 / 恢复 / 导出 ZIP), a definition list, a versions list that fills the remaining height, a note entry, ROM binding controls and a close action.
- Preserve the semantic three-area structure (platform dock / gallery / detail drawer) while allowing the drawer to overlay the gallery. The only page-level accent fill is the blue 备份 button.
