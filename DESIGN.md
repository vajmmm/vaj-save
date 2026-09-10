---
name: vaj-save Console Dark
description: Handheld console archive & backup manager design system
colors:
  primary: "#0a84ff"
  secondary: "#48484a"
  bg: "#1c1c1e"
  side: "#2c2c2e"
  card: "#3a3a3c"
  line: "#48484a"
  text: "#f5f5f7"
  muted: "#8e8e93"
  blue: "#0a84ff"
  green: "#30d158"
  orange: "#ff9f0a"
  red: "#ff453a"
  psp: "#64d2ff"
  vita: "#63e6be"
  switch: "#ff453a"
  threeds: "#ffd60a"
  nds: "#bf5af2"
  gba: "#30d158"
typography:
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
rounded:
  none: 0px
  sm: 2px
  md: 4px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "#ffffff"
    rounded: "{rounded.sm}"
    padding: 8px
  button-neutral:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.sm}"
    padding: 7px
  card:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
    padding: 12px
  sidebar:
    backgroundColor: "{colors.side}"
    textColor: "{colors.text}"
  platform-pip:
    backgroundColor: "{colors.primary}"
    width: 3px
  status-success:
    textColor: "{colors.green}"
    backgroundColor: "{colors.bg}"
  status-warning:
    textColor: "{colors.orange}"
    backgroundColor: "{colors.bg}"
  status-danger:
    textColor: "{colors.red}"
    backgroundColor: "{colors.bg}"
  tag-psp:
    textColor: "{colors.psp}"
    backgroundColor: "{colors.side}"
  tag-vita:
    textColor: "{colors.vita}"
    backgroundColor: "{colors.side}"
  tag-switch:
    textColor: "{colors.switch}"
    backgroundColor: "{colors.side}"
  tag-3ds:
    textColor: "{colors.threeds}"
    backgroundColor: "{colors.side}"
  tag-nds:
    textColor: "{colors.nds}"
    backgroundColor: "{colors.side}"
  tag-gba:
    textColor: "{colors.gba}"
    backgroundColor: "{colors.side}"
  input:
    backgroundColor: "{colors.card}"
    textColor: "{colors.text}"
    padding: 6px
  divider:
    backgroundColor: "{colors.line}"
---

## Overview

Handheld Console Laboratory meets Modern Desktop Utility.

`vaj-save` is a desktop application designed for backing up, versioning, inspecting, and restoring handheld console saves (PSP, PS Vita, Nintendo Switch, 3DS, NDS, GBA). 

Its design philosophy combines:
- **Console Dark Aesthetics**: Deep gray/black matte surfaces (`#1c1c1e`, `#2c2c2e`, `#3a3a3c`) inspired by gaming console system menus (PlayStation & Nintendo Switch OS).
- **Platform Color Identity**: High-recognition neon accent colors dedicated to each console family (PSP cyan, Vita turquoise, Switch neon red, 3DS gold, NDS purple, GBA green).
- **Dense, Functional 3-Column Ergonomics**: Clear left-to-right information hierarchy (Platforms/Devices → Saves Index → Details/Versions/Actions).
- **Rock-solid Native Desktop UI**: Flat controls, crisp contrast, zero layout jumps, and pinned action buttons that never slip off-screen.

## Colors

The application palette is defined in `vajsave.app_ui` and `vajsave.pixel_style`:

### Structural Neutrals
- **Background (`#1c1c1e`)**: Deep matte canvas foundation for the main window, header, and toolbar.
- **Side Panels (`#2c2c2e`)**: Secondary background for the left column (platforms/devices), middle column, and right detail container.
- **Cards & Inputs (`#3a3a3c`)**: Elevated surfaces for listboxes, search entries, note inputs, and active hover/selection states.
- **Divider Lines (`#48484a`)**: Subtle structural borders between sections and active button borders.

### Text & Information
- **Primary Text (`#f5f5f7`)**: High-contrast white for headers, game titles, and list labels.
- **Muted Text (`#8e8e93`)**: Secondary metadata, volume paths, slot timestamps, and device counts.

### Handheld Platform Identifiers
Each handheld platform has a strict brand identity color:
- **All Platforms (`#0a84ff`)**: Universal system blue.
- **PSP (`#64d2ff`)**: Sony PSP classic sky cyan.
- **PS Vita (`#63e6be`)**: PS Vita turquoise mint.
- **Nintendo Switch (`#ff453a`)**: Switch Joy-Con neon red.
- **Nintendo 3DS (`#ffd60a`)**: 3DS bright lemon gold.
- **Nintendo DS (`#bf5af2`)**: NDS deep violet.
- **Game Boy Advance (`#30d158`)**: GBA classic vibrant emerald green.

### Functional Status Colors
- **Action Accent (`#0a84ff`)**: Primary call to action (e.g., "备份" button).
- **Success (`#30d158`)**: Successful backup notification, matched checksums.
- **Warning (`#ff9f0a`)**: Changed saves, pending backup states.
- **Danger (`#ff453a`)**: Unmountable volumes, missing paths, destructive prompts.

## Typography

Typography prioritizes clarity, cross-platform legibility, and information density:
- **Font Stack**:
  - macOS: `PingFang SC`
  - Windows: `Microsoft YaHei`
  - Linux: `Noto Sans CJK SC`, `sans-serif`
  - Retro pixel assets: `PressStart2P-Regular.ttf` for special HUD badges and branding.
- **Scale Hierarchy**:
  - App Title: 22pt bold (`font=("...", 22, "bold")`)
  - Detail Title: 16pt bold (`font=("...", 16, "bold")`)
  - Game List & Primary Labels: 13pt–14pt
  - Metadata, Badges & Footers: 12pt

## Layout

The window is structured into an intuitive desktop layout:
- **Dimensions**: Min-size `1080x680`, default geometry `1180x740`.
- **Top Header & Toolbar**:
  - Header: App title + subtitle + right-aligned stats counter.
  - Toolbar: Search input (`fill=X, expand=True`) with quick action buttons ("刷新", "打开文件夹", "打开本地库", "设置") and monitoring checkbuttons.
- **3-Column Main Body**:
  1. **Left Panel (220px fixed)**:
     - Platform category list with 3px color indicator pip and count badges.
     - Detected volume/drive device listbox.
  2. **Middle Panel (Flexible width)**:
     - Game/save entries listbox with extended selection support.
  3. **Right Panel (340px fixed)**:
     - Top/Center: Scrollable canvas containing game title, detailed metadata, version history listbox, and editable note input.
     - Bottom: **Pinned action button stack** (`pack(side=BOTTOM)`) ensuring primary actions ("备份", "恢复", "导出 ZIP", "收藏", "在访达中显示") remain permanently accessible regardless of window height.
- **Bottom Bar**: Path display entry and status footer label.

## Elevation & Depth

- **Flat Console Styling**: Uses Tkinter `ttk` "clam" style with flat relief.
- **No Heavy Drop Shadows**: Visual hierarchy is created through layered flat background contrast (`#1c1c1e` → `#2c2c2e` → `#3a3a3c`), not artificial blur drop-shadows.
- **Separation Borders**: Elements and inputs use clean 1px border lines (`#48484a`) or flat boundary padding.

## Shapes

- **Subtle Rounding / Flat Edges**: Rectangular components with 0px to 4px corners (`rounded-sm` / `rounded-none`).
- **Platform Pip**: A distinct 3px-wide vertical indicator strip (`pip = tk.Frame(row, bg=platform_color, width=3)`) hugging the left edge of each platform selection row.
- **Inputs & Buttons**: Flat rectangles with balanced inner padding (`padding=(14, 7)` for buttons, `padding=6` for entries).

## Components

### Buttons
- **Accent Button (`Accent.TButton`)**:
  - Background `#0a84ff`, Foreground `#ffffff`, Active `#409cff`.
  - Reserved for primary operations ("备份").
- **Standard Button (`TButton`)**:
  - Background `#3a3a3c`, Foreground `#f5f5f7`, Active `#48484a`, Pressed `#2c2c2e`.
  - Used for secondary actions ("导出 ZIP", "恢复", "设置").

### Listboxes & Trees
- Background `#2c2c2e` / `#3a3a3c`, Foreground `#f5f5f7`.
- Selected item: Background `#0a84ff`, Text `#ffffff`, flat relief, zero border, row height 32px.

### Inputs & Text Entries
- Field background `#3a3a3c`, Text `#f5f5f7`, Insert cursor `#f5f5f7`, Border `#48484a`.

### Platform Indicator Rows
- Interactive row (`cursor="hand2"`), background `#3a3a3c` when selected, `#2c2c2e` when idle.
- 3px color pip on the left edge, label in text color, count badge on the right in muted color.

## Do's and Don'ts

### Do's
- **DO** use the defined platform colors (`PLATFORM_COLORS`) when rendering or tagging PSP, Vita, Switch, 3DS, NDS, or GBA items.
- **DO** keep the 3-column desktop hierarchy intact: Platforms/Devices on the left, Games in the middle, Details/Versions/Actions on the right.
- **DO** pin action buttons to the bottom of the right panel so they never get pushed off-screen by long detail text.
- **DO** maintain the console dark palette (`#1c1c1e` / `#2c2c2e` / `#3a3a3c`).
- **DO** support cross-platform font fallbacks (`PingFang SC` on macOS, `Microsoft YaHei` on Windows).

### Don'ts
- **DON'T** introduce light/white backgrounds or generic bright SaaS theme colors that clash with the console dark interface.
- **DON'T** use random saturated colors for platforms—always reference the canonical platform color map.
- **DON'T** allow the right-side detail content to overflow and clip the primary action buttons.
- **DON'T** hardcode Windows drive letters (e.g. `D:`) or OS-specific paths—always use cross-platform path handling.
