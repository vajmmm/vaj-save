---
name: vaj-save Archive Desk
description: Desktop archive workspace for handheld console save backups
colors:
  bg: "#ebebeb"
  surface: "#f2f2f2"
  surface_alt: "#e7e7e7"
  card: "#ffffff"
  text: "#2d2d2d"
  text_strong: "#1f2733"
  ink: "#14233b"
  muted: "#8b8b8b"
  muted_strong: "#687383"
  line: "#d6d6d6"
  line_soft: "#e3e6ea"
  border_soft: "#e1e7f0"
  window: "#ffffff"
  panel: "#ffffff"
  panel_alt: "#f7f9fc"
  line_strong: "#b0b0b0"
  hover: "#e2e2e2"
  accent: "#0a84ff"
  selected: "#e4f1ff"
  selected_soft: "#eaf3ff"
  accent_hover: "#409cff"
  on_accent: "#ffffff"
  primary: "#0a84ff"
  success: "#30d158"
  warning: "#ff9f0a"
  danger: "#ff453a"
  status_blue: "#2563eb"
  status_green: "#16a34a"
  status_orange: "#f59e0b"
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
  top-brand-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    height: 68px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
  list-row:
    backgroundColor: "{colors.window}"
    height: 70px
    pipWidth: 3px
    coverSize: 52px
    coverRadius: 6px
    selectedBackgroundColor: "#eaf3ff"
    hoverBackgroundColor: "{colors.hover}"
    textColor: "{colors.text}"
    mutedTextColor: "{colors.muted}"
  button-primary:
    backgroundColor: "{colors.accent}"
    hoverColor: "{colors.accent_hover}"
    textColor: "{colors.on_accent}"
    height: 32px
    rounded: "{rounded.sm}"
  button-neutral:
    backgroundColor: "{colors.surface_alt}"
    hoverColor: "{colors.hover}"
    textColor: "{colors.text}"
    height: 30px
    rounded: "{rounded.sm}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    borderColor: "{colors.line}"
    padding: 6px
  platform-pip:
    backgroundColor: "{colors.panel}"
    width: 3px
  divider:
    backgroundColor: "{colors.line}"
  bottom-system-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
---

## Overview

A dense desktop archive workspace for handheld console saves (PSP, PS Vita,
Nintendo Switch, 3DS, NDS, GBA). The visual target is a white, high-contrast
three-column workbench: navigation on the left, a data-rich archive table in
the center and an actionable inspector on the right.

Design philosophy:
- **Archive table, not a card gallery.** The middle column remains a flat,
  single-column（单列）list, but each row carries the information needed for a fast
  decision: cover, title, subtitle, platform, Title ID, latest backup and
  version count. Selection is a pale blue row tint, never a floating card.
- **Inspector on the right.** Selection opens a complete action surface with a
  large cover, metadata, a full-width blue 备份 action, secondary actions,
  version history and an editable note.
- **Platform identity stays compact.** Platform rows use small geometric marks
  and counts; save rows use the platform name and a restrained accent marker.
- **Status is scannable.** Status remains written out ("新" / "有变化" /
  "已备份"), with a small semantic dot and version count beneath it.

## Colors

All values live in `src/vajsave/ui_theme.py` (`SWITCH`, `PLATFORM_COLORS`, and
the module-level `HOVER` / `LINE_STRONG` / `ACCENT_HOVER` aliases) and are
re-exported by `src/vajsave/app_ui.py`. Nothing in the UI may hardcode a colour
literal.

### Structural surfaces (top to bottom)
- **Window (`#ffffff`)** — the main canvas and all three work areas.
- **Panel alt (`#f7f9fc`)** — the local library card and empty cover placeholder.
- **Subtle borders (`#e1e7f0`)** — column separators, inputs, table borders and row dividers.
- **Legacy tokens (`#ebebeb` / `#f2f2f2`)** remain exported for compatibility with
  the existing pure-theme tests; new widgets use the white workspace tokens.
- **Cards / inputs (`#ffffff`)** — the device Listbox, the versions Listbox and entries.
- **Hover (`#e2e2e2`)** — the face of a hovered row and of a hovered neutral button.
- **Divider lines (`#d6d6d6`)** — 1px row separators; **strong lines (`#b0b0b0`)** for emphasised edges.

### Text & information
- **Primary ink (`#14233b`)** — titles, list labels, definition values and numbers.
- **Muted ink (`#687383`)** — metadata, subtitles, timestamps and captions.

### Interaction
- **Accent (`#0a84ff`)** — the primary 备份 button and the selected platform mark. The 备份 button is the only saturated accent fill used as a large surface. In local-library browse mode the same primary slot renders as a neutral 删除备份 button, so the app never paints two saturated action surfaces at once.
- **Accent hover (`#409cff`)** — the 备份 button while hovered.
- **Selected list row (`#eaf3ff`, a tint of the accent)** — the default selection background for navigation, Listbox rows and save rows.

### Handheld platform identifiers
- **All (`#0a84ff`)**, **PSP (`#64d2ff`)**, **PS Vita (`#63e6be`)**, **Nintendo Switch (`#ff3c28`)**, **Nintendo 3DS (`#ffd60a`)**, **Nintendo DS (`#bf5af2`)**, **Game Boy Advance (`#30d158`)** — used in compact platform marks and row accents.

### Functional status colors
- **Success (`#16a34a`)**, **Warning (`#f59e0b`)**, **Danger (`#ff453a`)** — used for status dots, confirmations and destructive prompts.

## Typography

Cross-platform stack: macOS `PingFang SC`, Windows `Microsoft YaHei`, Linux
`Noto Sans CJK SC`, then `sans-serif`.
- App title 17pt bold, detail heading 15pt bold, list titles 13pt, list subtitles
  and status 11–12pt, panel captions 12pt.

## Layout

The window is composed of three vertical bands: **顶部品牌栏** (top brand bar) →
**三栏主体** (three-column body) → **底部操作栏** (combined action/status bar).

- **Dimensions**: min-size `1320x780`, default geometry `1480x900`.
- **1. Top brand bar (`#ffffff`)** — app identity on the left ("vaj-save" plus
  a subtitle) and archive statistics on the right. The mark is geometric and
  restrained: no round monogram badge and no clock.
- **2. Three-column body**:
  1. **Left panel (236px fixed, `#ffffff`)**: platform navigation rows with a
     geometric mark, label and count above the detected **device Listbox**
     (`tk.Listbox`, white rows, blue selected row) and a local-library card.
  2. **Middle column (flexible, `#ffffff`)**: the **single-column（单列）archive
     table** — a `tk.Canvas` (`SaveList`) drawing one 70px row per *visible* save,
     with 游戏 / 平台 / Title ID / 最近备份 / 状态 columns.
  3. **Right panel (430px fixed, `#ffffff`)**: the **inspector** — a large cover
     and metadata block, a full-width primary 备份 action, three secondary actions,
     the **versions Listbox** and an editable note.
- **3. Bottom status bar (`#ffffff`)**: connection state, archive state and warning
  text only. Quick actions live next to the relevant content instead of forming a
  bottom button wall.

## Save List Behavior

The middle column is a `SaveList` Canvas, not a Listbox and not a grid. The
number of rows always equals `len(state.visible_saves())`.

A row is `PAD + index * 70` tall and spans the flexible center column:
a 3px neutral edge and a 52px cover square lead into the title and subtitle;
platform, Title ID and latest backup occupy the metadata columns, while the
status column shows a small semantic dot, status text and version count. There
is no selection grow, ring, star or full-width platform bar.

- **Single click** — selects exactly one row (clears the rest) and shows its details.
- **Ctrl/Command + click** — toggles that row in the multi-selection.
- **Shift + click** — selects the contiguous range from the anchor row to the clicked row.
- **Ctrl/Command + A** — selects every visible row.
- **Arrow keys** — `↑/↓` move the active row by one; selection follows and clamps at the edges instead of wrapping.
- **Double click / Return** — activates the row and runs the primary 备份 action.
- **Hover** (`<Enter>` / `<Motion>` / `<Leave>`) — the row under the pointer repaints on the `hover` face; leaving clears it.
- **Auto-scroll** — whenever the selection moves, the row is scrolled into view (`yview_moveto`).
- **Wheel** — scrolls the list. There is no visible scrollbar chrome.

The device Listbox (`vol_list`) and versions Listbox (`version_list`) remain
`tk.Listbox` widgets and keep their native wheel handling.

## Elevation & Depth

- **Flat by design**: `ttk` "clam" style, flat relief and no blur drop-shadows.
- **Separator layering** creates depth: `#ffffff` workspace → `#f7f9fc` utility
  card → `#e1e7f0` borders.
- **Selection** is expressed with a light accent tint (`#e4f1ff`), never by
  growing or outlining a card.

## Shapes

- **List rows**: flat full-width bands separated by 1px lines; the hover/selection
  face uses a 6px rounded rectangle.
- **Cover squares**: 52px list squares and a 108×144 detail cover with an 8px radius; a lighter radius is used
  for the empty placeholder so it never reads as artwork.
- **Buttons**: `CanvasButton` draws a 6px rounded rectangle with a 1px outline.
- **Platform marks**: small geometric marks carry platform identity without
  turning the navigation into a wall of saturated strips.

## Components

### Save Row
The only representation of a save in the middle column (70px tall):
- **3px neutral edge** at the left edge; platform identity is carried by the
  geometric platform mark in the left rail rather than a repeated colour strip.
- **52px cover square** — the cover from `vajsave.covers.resolve_cover` (an
  embedded console icon found during the scan, then a user cover at
  `<library_root>/covers/<platform>/<name>.<ext>`), or a plain `#e7e7e7` square
  when nothing resolves. Covers are cover-cropped to a square and rounded.
- **Title** (13pt) and optional **subtitle** (11pt, muted) — the title_id / slot /
  user joined with " · ".
- **Status** — a semantic dot, the status text ("新", "有变化" or "已备份") and
  the number of versions.

Rows are separated by a 1px `#d6d6d6` line. Hover paints the row face `#e2e2e2`;
selection paints it the light accent tint. Rows never grow or ring on selection.

### Detail Inspector
Right panel, top to bottom:
- **Cover and identity**: a 108×144 cover preview beside the game name and
  subtitle.
- **Metadata grid**: platform, Title ID, version, size, latest backup, status and
  path use stable label/value columns so a long path cannot cover another field.
- **Primary action**: a full-width blue 备份 button. While browsing the local
  library it becomes a neutral 「删除备份」 button instead: the saturated accent
  stays reserved for backing a device save up, and the action deletes the
  selected games' local snapshots and covers after a confirmation.
- **Secondary actions**: 恢复, 导出 ZIP and 打开位置 in one measured row.
  - **恢复** first asks for confirmation and states that the selected version is
    copied to a folder and the handheld console is not written to; cancelling the
    confirmation copies nothing. It calls `restore_snapshot`, which only copies
    out of the library and never prunes the existing versions.
- **Game-identity binding (contextual)**: shown only when the selected save still
  needs help. An `ambiguous` save lists its candidate ROMs and offers a measured
  绑定所选 ROM button; an `unresolved` GBA/NDS save offers a 手动选择 ROM… button
  that opens the file dialog. The section stays hidden for every other save.
- **Versions Listbox**: white, blue selection, with a compact monospace row and a
  grid row that occupies the remaining inspector height.
- **Note entry**: a white input pinned to the bottom.

The path is one row in the metadata grid; there is no separate full-width path strip.

### Settings & Help (top brand bar)
Two quiet neutral buttons sit at the right of the top brand bar; neither uses a
saturated accent fill.
- **设置** opens a non-resizable dialog listing the local backup library path, the
  optional GBA/NDS ROM directories and the libretro metadata directory, plus the
  **保留版本数 (`keep_last`)** field. `keep_last` is read from and written back to
  the library's `settings.json`; `0` means unlimited. A blank/negative/non-numeric
  entry is rejected with a status warning, the dialog stays open, and
  `settings.json` is left untouched. Changing the value never prunes existing
  versions — pruning still happens only when a new version is added.
- **Optional LLM cover disambiguation** sits below `keep_last`: an 启用 toggle, a
  协议 selector (`openai` / `anthropic`), a masked API Key field and the
  endpoint/model fields (Base URL and 模型). Gemini is deliberately not offered
  and any unknown protocol value falls back to `openai`. The toggle, the key,
  the protocol, the base URL and the model are persisted to `config.json` (so
  they survive a restart); a blank base URL or model falls back to the selected
  protocol's built-in default rather than persisting an unusable configuration.
  Switching protocol rewrites only a still-default base URL (and model) to the
  new protocol's default — a customised gateway endpoint is left untouched. The
  OpenAI shape posts to `<base>/chat/completions` with a `Bearer` token; the
  Anthropic shape posts to `<base>/messages` with `x-api-key` and
  `anthropic-version` headers. The key is written to disk but never echoed into
  status/warning text or any log line or `repr`. When enabled,
  the LLM is consulted *only* for a genuinely ambiguous `Named_Boxarts` listing —
  several *different* titles matching one query — and must return one exact file
  name from the offered list. Region/language variants of a single title are
  still resolved deterministically to the USA release without any LLM call, and
  a disabled/blank configuration keeps multi-candidate behaviour byte-for-byte
  as before (placeholder, no network). A failed, nonsense or `NONE` answer leaves
  the placeholder in place and writes nothing to the cover cache.
- **帮助** opens a short, non-resizable guide. Its copy makes the archive
  direction explicit ("把掌机存档备份到电脑，不会写入掌机") and separates 备份 from
  the copy-to-folder 恢复. It replaces the old "帮助中心暂未配置" placeholder status.
  The guide also names the FTP pull and its Checkpoint / ftpd presets.

### FTP Pull (left device rail)
A quiet neutral 「FTP 拉取」 button sits beside 「＋ 添加设备」 under the device
Listbox. It opens a non-resizable dialog that pulls handheld saves straight from
a console FTP server into the local library's `ftp-cache/` tree, then scans that
cache like any other device.
- **Presets**: `Checkpoint` is the default; `ftpd` is a switchable fallback. The
  active preset, host, port and user are persisted; the password is kept in
  memory only and never written to `config.json` or any log/status line.
- **Read-only**: the client only ever lists and downloads. No upload/delete
  command is issued, and downloads are confined to
  `<library>/ftp-cache/<preset>/` (unsafe remote names are skipped).
- **Atomic**: files are staged in a hidden directory and the cache is swapped in
  only after the whole tree downloads. A failed or partial pull removes the
  staging directory, keeps the previous cache, and never becomes the selected
  device — the error is surfaced as a status warning instead.
- The pulled cache appears in the device list as a non-removable `FTP · <label>`
  row and is scanned with the normal platform scanners, so a 3DS/Switch
  Checkpoint export pulled over FTP behaves exactly like a mounted card.

### Platform Rows
`hand2` cursor; selected row tinted `#eaf3ff`, idle rows `#ffffff`. A small
geometric platform mark uses the canonical platform colour, while the row itself
stays neutral. Rows are **created once** and only their colours/texts are refreshed by
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
- **Toggle state**: `selected` (used by "监听插拔" / "仅显示有更新") tints the button.
- **API**: `set_text`, `set_selected`, `invoke`.
- **Sizing**: height `30px` in the status bar, `34px` in secondary actions and
  `40px` in the primary inspector action; width is measured from the label font
  unless the primary action fills its column.

### Inputs
White field background, dark text, `#d6d6d6` border, 6px padding.

## Do's and Don'ts

### Do's
- **DO** pull every colour from `vajsave.ui_theme` (`SWITCH`, `PLATFORM_COLORS`) — no ad-hoc hex literals in widgets.
- **DO** keep the vertical structure: top brand bar → three-column body → bottom status bar.
- **DO** render saves as a single-column list of flat rows; one row per visible save.
- **DO** show the resolved cover as a small square, falling back to a plain light-gray placeholder.
- **DO** write the status out as text with a small semantic dot.
- **DO** keep the secondary inspector actions measured; the primary 备份 action may fill the inspector width.
- **DO** let the versions list fill the remaining height of the inspector.
- **DO** use `CanvasButton` for every button and toggle so states stay consistent.
- **DO** keep the device and versions lists as `tk.Listbox`.
- **DO** use the canonical platform colours for platform marks and row accents only.

### Don'ts
- **DON'T** reintroduce the Console Dark surfaces (`#1c1c1e`, `#2c2c2e`, `#3a3a3c`) as UI background/panel/card colours.
- **DON'T** render saves as cards/grids or add grow/ring/star/bar/monogram decorations.
- **DON'T** use large coloured status badges; status dots must stay small and sit beside the text.
- **DON'T** fall back to `ttk.Button` / `ttk.Checkbutton`; bypassing `CanvasButton` breaks the Basic White chrome.
- **DON'T** use random saturated colours for a platform — always reference `PLATFORM_COLORS`.
- **DON'T** add a full-width secondary button wall or a full-width path strip to the inspector.
- **DON'T** hardcode Windows drive letters (e.g. `D:`) or OS-specific paths — use cross-platform path handling.

## Implementation Notes

- `src/vajsave/ui_theme.py` — pure tokens + helpers (`mix`, `lighten`, `darken`, `hex_to_rgb`, `rgb_to_hex`, `status_label`, `save_row`) plus the list row metrics (`ROW_HEIGHT`/`ROW_COVER`/`ROW_COVER_RADIUS`/`ROW_PIP_WIDTH`); display-free and unit tested in `tests/test_ui_theme.py`.
- `src/vajsave/covers.py` — read-only, exception-safe cover discovery and thumbnailing (`find_embedded_cover`, `user_cover_path`, `resolve_cover`, `load_thumbnail`); never imports tkinter and is unit tested in `tests/test_covers.py`.
- `src/vajsave/app_ui.py` — `CanvasButton` (rounded Canvas button/checkbutton), `SaveList` (single-column save list + bounded cover cache), and `VajSaveApp` (window wiring).
- `src/vajsave/scanner.py` — fills `SaveEntry.cover_path` with an embedded icon during the (bounded, read-only) scan.
- `src/vajsave/remote_ftp.py` — read-only FTP client and presets (`RemoteFtpClient`, `FtpProfile`, `ensure_read_only`, `sanitize_component`); password-free `repr`, no mutating verbs, injected transport for tests.
- `src/vajsave/ftp_fetch.py` — atomic preset pull into `<library>/ftp-cache/<preset>/` (`pull_preset`, `FtpPullResult`, `ftp_cache_root`); staged download + swap so a partial pull never becomes a device.
- `scripts/ui_preview.py` — builds the app offscreen and writes `build/ui-preview/home-preview.png` (Pillow) plus `save-list.eps` (converted to PNG when Ghostscript is available; degrades gracefully and still exits 0). The Pillow image is an **illustrative mock**, not a screenshot of the live widgets.
