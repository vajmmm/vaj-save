---
name: vaj-save Switch Basic White
description: Handheld console archive & backup manager design system
colors:
  bg: "#ebebeb"
  surface: "#f2f2f2"
  surface_alt: "#e7e7e7"
  card: "#ffffff"
  text: "#2d2d2d"
  muted: "#8b8b8b"
  line: "#d6d6d6"
  line_strong: "#b0b0b0"
  hover: "#e2e2e2"
  accent: "#0a84ff"
  accent_hover: "#409cff"
  ring: "#00a2ff"
  ring_tint: "#d6ecff"
  star: "#ffcc00"
  on_accent: "#ffffff"
  primary: "#0a84ff"
  success: "#30d158"
  warning: "#ff9f0a"
  danger: "#ff453a"
  green: "#30d158"
  orange: "#ff9f0a"
  red: "#ff453a"
  all: "#0a84ff"
  psp: "#64d2ff"
  vita: "#63e6be"
  switch: "#ff3c28"
  3ds: "#ffd60a"
  threeds: "#ffd60a"
  nds: "#bf5af2"
  gba: "#30d158"
typography:
  fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
  title:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 22px
    fontWeight: 700
    lineHeight: 1.3
  heading:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 16px
    fontWeight: 700
    lineHeight: 1.4
  body:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 13px
    lineHeight: 1.5
  detail:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 12px
    lineHeight: 1.4
  monogram:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 24px
    fontWeight: 700
rounded:
  none: 0px
  sm: 8px
  md: 12px
  lg: 16px
  tile: 14px
  pill: 11px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
components:
  top-status-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    height: 64px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.lg}"
  tile:
    backgroundColor: "{colors.card}"
    width: 116px
    height: 124px
    gap: 14px
    faceTint: 0.86
    textColor: "{colors.text}"
    rounded: "{rounded.tile}"
    barHeight: 4px
    coverInset: 6px
    coverHeight: 66px
    coverRadius: 12px
  tile-hover:
    backgroundColor: "{colors.hover}"
    borderColor: "{colors.line}"
    borderWidth: 1px
    rounded: "{rounded.tile}"
  tile-selected:
    borderColor: "{colors.ring}"
    borderWidth: 3px
    ringGap: 6px
    grow: 8px
    rounded: "{rounded.tile}"
  button-primary:
    backgroundColor: "{colors.accent}"
    hoverColor: "{colors.accent_hover}"
    textColor: "{colors.on_accent}"
    height: 36px
    rounded: "{rounded.sm}"
  button-neutral:
    backgroundColor: "{colors.surface_alt}"
    hoverColor: "{colors.hover}"
    textColor: "{colors.text}"
    height: 34px
    rounded: "{rounded.sm}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    borderColor: "{colors.line}"
    padding: 6px
  platform-pip:
    backgroundColor: "{colors.primary}"
    width: 3px
  status-pill-new:
    textColor: "{colors.accent}"
    backgroundColor: "#ddeeff"
  status-pill-changed:
    textColor: "{colors.warning}"
    backgroundColor: "#fff0db"
  status-pill-unchanged:
    textColor: "{colors.success}"
    backgroundColor: "#e3f7e8"
  tag-psp:
    textColor: "{colors.psp}"
  tag-vita:
    textColor: "{colors.vita}"
  tag-switch:
    textColor: "{colors.switch}"
  tag-3ds:
    textColor: "{colors.threeds}"
  tag-nds:
    textColor: "{colors.nds}"
  tag-gba:
    textColor: "{colors.gba}"
  divider:
    backgroundColor: "{colors.line}"
  bottom-system-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
---

## Overview

Nintendo Switch HOME menu meets a dense desktop backup utility.

`vaj-save` backs up, versions, inspects, and restores handheld console saves (PSP, PS Vita, Nintendo Switch, 3DS, NDS, GBA). The interface follows the Switch **Basic White** system theme instead of the old Console Dark palette.

Design philosophy:
- **Basic White surfaces**: a light gray page (`#ebebeb`), softer gray panels (`#f2f2f2` / `#e7e7e7`), and pure white inputs carry every piece of content. No dark chrome.
- **Pastel platform tiles**: saves render as compact HOME-menu cover tiles in a Canvas grid — each tile shows the game's cover art (embedded console icon, then a user-supplied cover, then a pastel fallback face with a small monogram), a wrapped title, a top-left status pill, an optional `★`, and a full-width platform colour bar. The 116x124 tiles keep the grid dense (12 visible at the default window) instead of a sparse text-only list.
- **Platform color identity**: the canonical neon accents (PSP cyan, Vita turquoise, Switch Joy-Con red, 3DS gold, NDS violet, GBA green) are kept as bars, pips, and monograms so a light UI can still encode the platform at a glance.
- **Pinned, predictable actions**: the right-hand action stack is packed to the bottom (`pack(side=BOTTOM)`), so long detail text can never push the primary buttons off-screen.

## Colors

All values live in `src/vajsave/ui_theme.py` (`SWITCH`, `PLATFORM_COLORS`, and the module-level `HOVER` / `LINE_STRONG` / `STAR` / `ACCENT_HOVER` / `RING_TINT` aliases) and are re-exported by `src/vajsave/app_ui.py`. Nothing in the UI may hardcode a colour literal.

### Structural surfaces (top to bottom)
- **Page background (`#ebebeb`)** — the window canvas, the game-grid column, and the status line.
- **Panels (`#f2f2f2`)** — the top status bar, the left platform/device panel, the right detail panel, and the bottom system bar.
- **Subtle alt (`#e7e7e7`)** — neutral buttons, input borders, and inactive states.
- **Cards / inputs (`#ffffff`)** — the device Listbox, the versions Listbox, and entries.
- **Hover (`#e2e2e2`)** — the face of a hovered save tile and of a hovered neutral button.
- **Divider lines (`#d6d6d6`)** — tile outlines and 1px separators; **strong lines (`#b0b0b0`)** for emphasised edges.

### Text & information
- **Primary text (`#2d2d2d`)** — titles, game names, list labels, numbers.
- **Muted text (`#8b8b8b`)** — metadata, volume paths, counts, subtitles.

### Interaction
- **Accent (`#0a84ff`)** — the primary "备份" button, selected list rows, the "all" platform pip.
- **Accent hover (`#409cff`)** — the accent button while hovered.
- **Selection ring (`#00a2ff`)** — the 3px outline drawn around the focused save tile; **ring tint (`#d6ecff`)** is its soft fill companion.
- **Star (`#ffcc00`)** — the `★` marker on starred saves.

### Handheld platform identifiers
- **All (`#0a84ff`)**, **PSP (`#64d2ff`)**, **PS Vita (`#63e6be`)**, **Nintendo Switch (`#ff3c28`)**, **Nintendo 3DS (`#ffd60a`)**, **Nintendo DS (`#bf5af2`)**, **Game Boy Advance (`#30d158`)**.

### Functional status colors
- **Success (`#30d158`)** — "已备份" pills and confirmations.
- **Warning (`#ff9f0a`)** — "有变化" pills, pending states.
- **Danger (`#ff453a`)** — unmountable volumes, missing paths, destructive prompts.

## Typography

Same cross-platform stack as before; only the surfaces changed:
- macOS `PingFang SC`, Windows `Microsoft YaHei`, Linux `Noto Sans CJK SC`, then `sans-serif`.
- App title 22pt bold, detail title 16pt bold, tile titles 11pt, list labels 13pt, metadata 11–12pt.
- Tile titles are **11pt** and wrap inside the tile (`width=tileWidth-12`); the selected tile's title is bold while idle titles stay regular.
- Tile monograms (the fallback face when no cover resolves) are **24pt bold, centred** in the cover region — deliberately smaller so the tile is dominated by artwork or the title, not a huge empty square.

## Layout

The window is composed of four vertical bands: **顶部状态栏** (top status bar) → **三栏主体** (three-column body) → **底部系统栏** (bottom system bar) → **状态行** (status line).

- **Dimensions**: min-size `1080x680`, default geometry `1180x740`.
- **1. Top status bar (`#f2f2f2`, 64px)** — app identity on the left (round accent badge + "vaj-save" + subtitle) and the live clock + backup stats on the right. The search entry sits directly beneath it.
- **2. Three-column body**:
  1. **Left panel (220px fixed, `#f2f2f2`)**: platform filter rows (3px color pip + label + count badge, `hand2` cursor) above the detected **device Listbox** (`tk.Listbox`, white rows, blue selected row).
  2. **Middle column (flexible, `#ebebeb`)**: the **compact cover tile grid** — a `tk.Canvas` (`SaveTileGrid`) drawing 116x124 rounded tiles, one per *visible* save. At the default `1180x740` window this is **4 columns x 3 rows = 12 visible tiles**; at the `1080x680` minimum it stays at **>= 3 columns**.
  3. **Right panel (340px fixed, `#f2f2f2`)**: scrollable detail canvas (title, metadata, versions Listbox, note entry) with the **action stack pinned to the bottom** (`pack(side=BOTTOM)`).
- **3. Bottom system bar (`#f2f2f2`)**: quick actions ("刷新", "打开文件夹", "打开本地库", "设置") and the monitoring toggles ("监听插拔", "隐藏已备份"), all rendered as `CanvasButton`s so the 1080px minimum width never wraps or clips them.
- **4. Status line (`#ebebeb`)**: left-aligned status text and a right-aligned warning label.

## Tile Grid Behavior

The middle column is no longer a `Listbox`; it is a Canvas tile grid. The number of tiles always equals `len(state.visible_saves())`.

**Density**: tiles are `116x124` with a `14px` gap, so the flexible middle column fits **4 columns x 3 full rows = 12 visible tiles** at the default `1180x740` window (was `2x2 = 4` with the old 246px square tiles), and **>= 3 columns** at the `1080x680` minimum. Column count is always derived from the live canvas width via `ui_theme.grid_columns`, never hardcoded.

- **Single click** — selects exactly one tile (clears the rest) and shows its details.
- **Ctrl/Command + click** — toggles that tile in the multi-selection.
- **Shift + click** — selects the contiguous range from the anchor tile to the clicked tile.
- **Arrow keys** — `←/→` move the active tile by one column, `↑/↓` by one row; selection follows and clamps at the edges instead of wrapping.
- **Double click / Return** — activates the tile and runs the primary "备份" action.
- **Hover** (`<Enter>` / `<Motion>` / `<Leave>`) — the tile under the pointer repaints on the `hover` face with a 1px `line` outline; leaving clears it.
- **Auto-scroll** — whenever the selection moves, the tile is scrolled into view (`yview_moveto`).
- **Wheel** — scrolls the grid; the detail region keeps its own wheel routing.

The device Listbox (`vol_list`) and versions Listbox (`version_list`) remain `tk.Listbox` widgets; the version list intentionally leaves `<MouseWheel>`/`<Button-4>` unbound so Tk's native class binding still scrolls it.

## Elevation & Depth

- **Flat by design**: `ttk` "clam" style, flat relief, no blur drop-shadows.
- **Contrast layering** creates depth: `#ebebeb` page → `#f2f2f2` panel → pastel tile → `#ffffff` inputs/lists.
- **Selection** is expressed by growing the tile by 8px overall and drawing a 3px `#00a2ff` ring 6px outside it (`grow`/`ringGap` in `ui_theme`), not by a dark fill.

## Shapes

- **Rounded tiles**: compact cover tiles use a 14px corner radius (`rounded.tile`) drawn as a smoothed Canvas polygon.
- **Rounded cover art**: thumbnails are decoded to exact-size rounded RGBA images (`rounded.tile`-scaled 12px radius) so artwork never sits on a square white mat.
- **Rounded pills**: status badges use a fully rounded pill (`r = height/2`).
- **Rounded buttons**: `CanvasButton` draws an 8px rounded pill (`radius = 9`) with a 1px outline.
- **Panels**: 16px rounding conceptually; flat frames stay rectangular internally.
- **Pips**: a 3px vertical accent strip hugs the left edge of each platform row.

## Components

### Save Tile
Compact rounded card (`116x124`, 14px corners). The card face is a pastel tint (`mix(platform_colour, white, 0.86)`), and the upper **cover region** (inset 6px, height 66px) shows, in priority order:

1. the cover returned by `vajsave.covers.resolve_cover` — an embedded console icon found during the scan (fixed names `icon0.*` → `icon.*` → `pic1.*` → `thumb.*` → `preview.*` → `folder.*` → `cover.*` → `banner.*` → `boxart.*`, then a deterministic generic image scan of the save folder and its depth-1 sub-directories), then a user cover at `<library_root>/covers/<platform>/<name>.<ext>`;
2. otherwise a **24pt bold monogram** in the platform colour on the pastel face.

Below the artwork sits the wrapped 11pt title, an optional 9pt subtitle drawn just above the bottom bar, an optional `★` overlay (top-right, star colour) and the **status pill as a top-left corner badge overlaid on the artwork**. A **full-width 4px platform colour bar** hugs the bottom edge.

- **Hover**: face becomes `hover` (`#e2e2e2`) with a 1px `line` outline.
- **Selected**: the tile grows 8px overall, a 3px `ring` outline is drawn 6px outside it, and the title turns bold.
- **Covers**: thumbnails are loaded through `covers.load_thumbnail` (exact `104x66` rounded RGBA) and cached by path + mtime + size in a bounded, reference-keeping, negative-aware cache (max 256). The fit is a deliberate **cover crop**: the source is centre-cropped to the target aspect ratio *before* resizing, so the tile is always fully filled and very wide/tall artwork **loses its outer edges** (it is never stretched out of aspect or letterboxed). Concretely, against the `104x66` (1.576:1) cover region a 4:3 source keeps ~84.6% of its height (≈7.7% trimmed top and bottom) while a 16:9 source keeps ~88.6% of its width (≈5.7% trimmed per side). Files larger than `covers.MAX_COVER_BYTES` (8 MiB) are skipped up front, and resize targets are capped at `covers.MAX_COVER_RESIZE_DIMENSION` (4096), so a pathological aspect ratio (e.g. a 122-byte `5000x2` PNG) cannot allocate an oversized intermediate or block the UI. A missing/corrupt file silently falls back to the pastel face.

### Status Pills
- **新** — accent text on a light blue tint (`mix(accent, white, 0.86)`).
- **有变化** — warning text on a light orange tint.
- **已备份** — success text on a light green tint.
- Unknown statuses degrade to a muted grey pill labelled with the raw status.

### Platform Rows
`hand2` cursor; selected row tinted with `mix(accent, white, 0.84)`, idle rows `#f2f2f2`. 3px platform pip on the left, label in text colour, count badge on the right in muted grey. Rows are **created once** and only their colours/texts are refreshed by `refresh_platform_ui()`, so widget ids stay stable.

### Listboxes (devices, versions)
White background, dark text, blue (`#0a84ff`) selection with white foreground, flat relief, zero border.

### Buttons — `CanvasButton`
Every button and monitoring toggle is a `CanvasButton` (a `tk.Canvas` subclass); no `ttk.Button`/`ttk.Checkbutton` remains.

- **States**: `idle`, `hover` (`#e2e2e2` neutral / `#409cff` accent), `pressed`, and an `accent` variant for the primary "备份" / "保存" actions.
- **Toggle state**: `selected` (used by "监听插拔" / "隐藏已备份") tints the button and lights its optional left dot.
- **API**: `set_text`, `set_selected`, `invoke`.
- **Sizing**: height `34px` in the bottom system bar, `36px` in the right action stack; width is measured from the label font and grows with the text.

### Inputs
White field background, dark text, `#d6d6d6` border, 6px padding.

## Do's and Don'ts

### Do's
- **DO** pull every colour from `vajsave.ui_theme` (`SWITCH`, `PLATFORM_COLORS`) — no ad-hoc hex literals in widgets.
- **DO** keep the vertical structure: top status bar → three-column body → bottom system bar → status line.
- **DO** render saves as compact rounded Canvas cover tiles; one tile per visible save.
- **DO** show the resolved cover first (embedded icon → user cover → pastel), keep the aspect ratio by centre-cropping to the tile (cover fit; edges may be trimmed), and round the thumbnail corners.
- **DO** keep the status pill and `★` as small overlay badges on the cover, never as full-width bars that eat the artwork.
- **DO** fall back silently to the pastel face + small monogram when a cover is missing/corrupt.
- **DO** use `CanvasButton` for every button and toggle so states stay consistent.
- **DO** pin the right-panel action buttons to the bottom so long detail text cannot clip them.
- **DO** keep the device and versions lists as `tk.Listbox` and leave their native wheel handling intact.
- **DO** use the canonical platform colours for pips, bars, monograms, and cover fallbacks.

### Don'ts
- **DON'T** reintroduce the Console Dark surfaces (`#1c1c1e`, `#2c2c2e`, `#3a3a3c`) as UI background/panel/card colours.
- **DON'T** fall back to `ttk.Button` / `ttk.Checkbutton`; bypassing `CanvasButton` breaks the Basic White chrome.
- **DON'T** use random saturated colours for a platform — always reference `PLATFORM_COLORS`.
- **DON'T** stretch a cover out of aspect ratio or let it spill outside its 14px-rounded tile.
- **DON'T** name user cover files with Windows-illegal characters (`:`, `*`, `?`, …) — use the sanitized title/display name, never a `platform:title:slot` game key.
- **DON'T** let the tile grid overflow its column; clip or scroll it.
- **DON'T** hardcode tile sizes or columns — read them from `ui_theme` and derive columns with `grid_columns`.
- **DON'T** hardcode Windows drive letters (e.g. `D:`) or OS-specific paths — use cross-platform path handling.

## Implementation Notes

- `src/vajsave/ui_theme.py` — pure tokens + helpers (`mix`, `lighten`, `darken`, `hex_to_rgb`, `rgb_to_hex`, `ring_size`, `grid_columns`, `monogram`, `status_pill`, `tile_face`) plus the compact tile metrics (`TILE_WIDTH`/`TILE_HEIGHT`/`TILE_GAP`/`TILE_RADIUS`/cover geometry); display-free and unit tested in `tests/test_ui_theme.py`.
- `src/vajsave/covers.py` — read-only, exception-safe cover discovery and thumbnailing (`find_embedded_cover`, `user_cover_path`, `resolve_cover`, `load_thumbnail`); never imports tkinter and is unit tested in `tests/test_covers.py`.
- `src/vajsave/app_ui.py` — `CanvasButton` (rounded Canvas button/checkbutton), `SaveTileGrid` (compact cover tile grid + bounded cover cache), and `VajSaveApp` (window wiring).
- `src/vajsave/scanner.py` — fills `SaveEntry.cover_path` with an embedded icon during the (bounded, read-only) scan.
- `scripts/ui_preview.py` — builds the app offscreen and writes `build/ui-preview/home-preview.png` (Pillow) plus `save-grid.eps`/`detail.eps` (converted to PNG when Ghostscript is available; degrades gracefully and still exits 0). The Pillow image is an **illustrative mock**, not a screenshot of the live widgets.
