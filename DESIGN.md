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
    fontSize: 17px
    fontWeight: 700
    lineHeight: 1.3
  heading:
    fontFamily: PingFang SC, Microsoft YaHei, Noto Sans CJK SC, sans-serif
    fontSize: 15px
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
rounded:
  none: 0px
  sm: 6px
  md: 10px
  row: 6px
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
    height: 56px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
  list-row:
    backgroundColor: "{colors.bg}"
    height: 44px
    pipWidth: 3px
    coverSize: 32px
    coverRadius: 6px
    selectedBackgroundColor: "#e4f1ff"
    hoverBackgroundColor: "{colors.hover}"
    textColor: "{colors.text}"
    mutedTextColor: "{colors.muted}"
  button-primary:
    backgroundColor: "{colors.accent}"
    hoverColor: "{colors.accent_hover}"
    textColor: "{colors.on_accent}"
    height: 28px
    rounded: "{rounded.sm}"
  button-neutral:
    backgroundColor: "{colors.surface_alt}"
    hoverColor: "{colors.hover}"
    textColor: "{colors.text}"
    height: 28px
    rounded: "{rounded.sm}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    borderColor: "{colors.line}"
    padding: 6px
  platform-pip:
    backgroundColor: "{colors.primary}"
    width: 3px
  divider:
    backgroundColor: "{colors.line}"
  bottom-system-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
---

## Overview

A quiet, text-first desktop backup utility for handheld console saves (PSP,
PS Vita, Nintendo Switch, 3DS, NDS, GBA). The interface follows the Switch
**Basic White** system theme: light gray surfaces, near-black ink and a single
system-blue accent.

Design philosophy:
- **Quiet list, not showroom.** The middle column is a plain, single-column（单列）list
  of save rows. Each row carries only what you need to pick a save: a 3px
  platform pip, a small cover square (or light-gray placeholder), the title and a
  plain text status. No card grid, no grow-on-select, no glow ring, no star, no
  full-width platform bar and no oversized monogram.
- **Inspector on the right.** Selection opens a readable detail panel: a
  definition list of key/value facts, a versions list that fills the remaining
  height, and a note field. The only page-level colour fill is the blue 备份
  button.
- **Platform identity stays subtle.** The canonical neon accents (PSP cyan, Vita
  turquoise, Switch Joy-Con red, 3DS gold, NDS violet, GBA green) appear only as
  3px pips in the platform rows and list rows.
- **Everything is text.** Status is written out ("新" / "有变化" / "已备份") in
  muted ink instead of coloured badge chrome.

## Colors

All values live in `src/vajsave/ui_theme.py` (`SWITCH`, `PLATFORM_COLORS`, and
the module-level `HOVER` / `LINE_STRONG` / `ACCENT_HOVER` aliases) and are
re-exported by `src/vajsave/app_ui.py`. Nothing in the UI may hardcode a colour
literal.

### Structural surfaces (top to bottom)
- **Page background (`#ebebeb`)** — the window canvas, the save list and the status line.
- **Panels (`#f2f2f2`)** — the top status bar, the left platform/device panel, the right inspector and the bottom system bar.
- **Subtle alt (`#e7e7e7`)** — neutral buttons, input borders, inactive states and the empty cover placeholder square.
- **Cards / inputs (`#ffffff`)** — the device Listbox, the versions Listbox and entries.
- **Hover (`#e2e2e2`)** — the face of a hovered row and of a hovered neutral button.
- **Divider lines (`#d6d6d6`)** — 1px row separators; **strong lines (`#b0b0b0`)** for emphasised edges.

### Text & information
- **Primary text (`#2d2d2d`)** — titles, list labels, definition values, numbers.
- **Muted text (`#8b8b8b`)** — metadata, list subtitles, row status text, panel captions.

### Interaction
- **Accent (`#0a84ff`)** — the primary 备份 button and the "all" platform pip. The 备份 button is the only saturated accent fill on the page.
- **Accent hover (`#409cff`)** — the 备份 button while hovered.
- **Selected list row (`#e4f1ff`, a tint of the accent)** — the default selection background for Listbox rows and list rows.

### Handheld platform identifiers
- **All (`#0a84ff`)**, **PSP (`#64d2ff`)**, **PS Vita (`#63e6be`)**, **Nintendo Switch (`#ff3c28`)**, **Nintendo 3DS (`#ffd60a`)**, **Nintendo DS (`#bf5af2`)**, **Game Boy Advance (`#30d158`)** — used as 3px pips only.

### Functional status colors
- **Success (`#30d158`)**, **Warning (`#ff9f0a`)**, **Danger (`#ff453a`)** — reserved for confirmations and destructive prompts; status is otherwise written as text.

## Typography

Cross-platform stack: macOS `PingFang SC`, Windows `Microsoft YaHei`, Linux
`Noto Sans CJK SC`, then `sans-serif`.
- App title 17pt bold, detail heading 15pt bold, list titles 13pt, list subtitles
  and status 11–12pt, panel captions 12pt.

## Layout

The window is composed of four vertical bands: **顶部状态栏** (top status bar) →
**三栏主体** (three-column body) → **底部系统栏** (bottom system bar) → **状态行**
(status line).

- **Dimensions**: min-size `1080x680`, default geometry `1180x740`.
- **1. Top status bar (`#f2f2f2`)** — app identity on the left ("vaj-save" plus a
  single-line subtitle) and the backup stats on the right. No round monogram
  badge and no clock. The subtitle must stay fully visible at both the default
  and the minimum window size.
- **2. Three-column body**:
  1. **Left panel (220px fixed, `#f2f2f2`)**: platform filter rows (3px colour pip
     + label + count, `hand2` cursor) above the detected **device Listbox**
     (`tk.Listbox`, white rows, blue selected row).
  2. **Middle column (flexible, `#ebebeb`)**: the **single-column（单列）save list** — a
     `tk.Canvas` (`SaveList`) drawing one 44px row per *visible* save.
  3. **Right panel (360px fixed, `#f2f2f2`)**: the **inspector** — a header row
     ("详情" plus the three small actions 备份 / 恢复 / 导出 ZIP), a definition list
     of facts, the **versions Listbox** filling the remaining height, and a note
     entry pinned to the bottom.
- **3. Bottom system bar (`#f2f2f2`)**: quick actions ("刷新", "打开文件夹",
  "打开本地库", "设置") and the monitoring toggles ("监听插拔", "隐藏已备份"),
  all rendered as `CanvasButton`s.
- **4. Status line (`#ebebeb`)**: left-aligned status text and a right-aligned warning label.

## Save List Behavior

The middle column is a `SaveList` Canvas, not a Listbox and not a grid. The
number of rows always equals `len(state.visible_saves())`.

A row is `PAD + index * 44` tall and spans the column width minus `12px` padding:
a 3px platform pip hugs the left edge, then a 32px cover square (a rounded
thumbnail when a cover resolves, otherwise a plain `#e7e7e7` square), then the
title (13pt) with an optional 11pt subtitle, and the status text (12pt, muted)
right-aligned. There is no selection grow, no ring, no star, no platform bar and
no monogram.

- **Single click** — selects exactly one row (clears the rest) and shows its details.
- **Ctrl/Command + click** — toggles that row in the multi-selection.
- **Shift + click** — selects the contiguous range from the anchor row to the clicked row.
- **Ctrl/Command + A** — selects every visible row.
- **Arrow keys** — `↑/↓` move the active row by one; selection follows and clamps at the edges instead of wrapping.
- **Double click / Return** — activates the row and runs the primary 备份 action.
- **Hover** (`<Enter>` / `<Motion>` / `<Leave>`) — the row under the pointer repaints on the `hover` face; leaving clears it.
- **Auto-scroll** — whenever the selection moves, the row is scrolled into view (`yview_moveto`).
- **Wheel** — scrolls the list.

The device Listbox (`vol_list`) and versions Listbox (`version_list`) remain
`tk.Listbox` widgets and keep their native wheel handling.

## Elevation & Depth

- **Flat by design**: `ttk` "clam" style, flat relief, no blur drop-shadows.
- **Contrast layering** creates depth: `#ebebeb` page → `#f2f2f2` panel →
  `#ffffff` listboxes/inputs.
- **Selection** is expressed with a light accent tint (`#e4f1ff`), never by
  growing or outlining a card.

## Shapes

- **List rows**: flat full-width bands separated by 1px lines; the hover/selection
  face uses a 6px rounded rectangle.
- **Cover squares**: 32px squares with a 6px radius; a lighter 6px radius is used
  for the empty placeholder so it never reads as artwork.
- **Buttons**: `CanvasButton` draws a 6px rounded rectangle with a 1px outline.
- **Pips**: a 3px vertical accent strip hugs the left edge of each platform row and each save row.

## Components

### Save Row
The only representation of a save in the middle column (44px tall):
- **3px platform pip** at the left edge in the canonical platform colour.
- **32px cover square** — the cover from `vajsave.covers.resolve_cover` (an
  embedded console icon found during the scan, then a user cover at
  `<library_root>/covers/<platform>/<name>.<ext>`), or a plain `#e7e7e7` square
  when nothing resolves. Covers are cover-cropped to a square and rounded.
- **Title** (13pt) and optional **subtitle** (11pt, muted) — the title_id / slot /
  user joined with " · ".
- **Status text** (12pt, muted) right-aligned: "新", "有变化" or "已备份".

Rows are separated by a 1px `#d6d6d6` line. Hover paints the row face `#e2e2e2`;
selection paints it the light accent tint. Rows never grow or ring on selection.

### Detail Inspector
Right panel, top to bottom:
- **Header**: the "详情" caption with the three small actions (备份 accent, 恢复,
  导出 ZIP) packed to its right. Buttons are small and measured — never a
  full-width button wall.
- **Definition list**: 游戏名 as a 15pt heading followed by label/value rows
  (机种, 状态, 卡上时间, 上次备份, 路径). Long values wrap.
- **Versions Listbox**: white, blue selection, `expand=True` so it occupies all
  remaining height.
- **Note entry**: a white input pinned to the bottom.

There is no full-width path strip.

### Platform Rows
`hand2` cursor; selected row tinted `#e4f1ff`, idle rows `#f2f2f2`. 3px platform
pip on the left, label in text colour, count on the right in muted grey. Rows are
**created once** and only their colours/texts are refreshed by
`refresh_platform_ui()`, so widget ids stay stable.

### Listboxes (devices, versions)
White background, dark text, light accent tint (`#e4f1ff`) selection with dark
foreground, flat relief, zero border. The accent tint is the same quiet
selection used by the save list — the saturated accent is reserved for the 备份
button.

### Buttons — `CanvasButton`
Every button and monitoring toggle is a `CanvasButton` (a `tk.Canvas` subclass);
no `ttk.Button`/`ttk.Checkbutton` remains.

- **States**: `idle`, `hover` (`#e2e2e2` neutral / `#409cff` accent), `pressed`,
  and an `accent` variant for the primary 备份 action.
- **Toggle state**: `selected` (used by "监听插拔" / "隐藏已备份") tints the button.
- **API**: `set_text`, `set_selected`, `invoke`.
- **Sizing**: height `32px` in the bottom system bar, `28px` in the inspector
  header; width is measured from the label font.

### Inputs
White field background, dark text, `#d6d6d6` border, 6px padding.

## Do's and Don'ts

### Do's
- **DO** pull every colour from `vajsave.ui_theme` (`SWITCH`, `PLATFORM_COLORS`) — no ad-hoc hex literals in widgets.
- **DO** keep the vertical structure: top status bar → three-column body → bottom system bar → status line.
- **DO** render saves as a single-column list of flat rows; one row per visible save.
- **DO** show the resolved cover as a small square, falling back to a plain light-gray placeholder.
- **DO** write the status out as text in muted ink.
- **DO** keep the inspector's action buttons small and measured.
- **DO** let the versions list fill the remaining height of the inspector.
- **DO** use `CanvasButton` for every button and toggle so states stay consistent.
- **DO** keep the device and versions lists as `tk.Listbox`.
- **DO** use the canonical platform colours for the 3px pips only.

### Don'ts
- **DON'T** reintroduce the Console Dark surfaces (`#1c1c1e`, `#2c2c2e`, `#3a3a3c`) as UI background/panel/card colours.
- **DON'T** render saves as cards/grids or add grow/ring/star/bar/monogram decorations.
- **DON'T** add coloured status badges; status is plain text.
- **DON'T** fall back to `ttk.Button` / `ttk.Checkbutton`; bypassing `CanvasButton` breaks the Basic White chrome.
- **DON'T** use random saturated colours for a platform — always reference `PLATFORM_COLORS`.
- **DON'T** add a full-width button wall or a full-width path strip to the inspector.
- **DON'T** hardcode Windows drive letters (e.g. `D:`) or OS-specific paths — use cross-platform path handling.

## Implementation Notes

- `src/vajsave/ui_theme.py` — pure tokens + helpers (`mix`, `lighten`, `darken`, `hex_to_rgb`, `rgb_to_hex`, `status_label`, `save_row`) plus the list row metrics (`ROW_HEIGHT`/`ROW_COVER`/`ROW_COVER_RADIUS`/`ROW_PIP_WIDTH`); display-free and unit tested in `tests/test_ui_theme.py`.
- `src/vajsave/covers.py` — read-only, exception-safe cover discovery and thumbnailing (`find_embedded_cover`, `user_cover_path`, `resolve_cover`, `load_thumbnail`); never imports tkinter and is unit tested in `tests/test_covers.py`.
- `src/vajsave/app_ui.py` — `CanvasButton` (rounded Canvas button/checkbutton), `SaveList` (single-column save list + bounded cover cache), and `VajSaveApp` (window wiring).
- `src/vajsave/scanner.py` — fills `SaveEntry.cover_path` with an embedded icon during the (bounded, read-only) scan.
- `scripts/ui_preview.py` — builds the app offscreen and writes `build/ui-preview/home-preview.png` (Pillow) plus `save-list.eps` (converted to PNG when Ghostscript is available; degrades gracefully and still exits 0). The Pillow image is an **illustrative mock**, not a screenshot of the live widgets.
