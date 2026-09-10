import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional, Tuple, Union

from .app_state import BACKUP_STATUS_LABELS, PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .library import Snapshot
from .models import SaveEntry, VolumeInfo
from .ui_theme import (
    DEFAULT_TILE_GAP,
    DEFAULT_TILE_WIDTH,
    PLATFORM_COLORS,
    SWITCH,
    TILE_BAR_HEIGHT,
    TILE_RADIUS,
    TILE_RING_GAP,
    TILE_RING_WIDTH,
    TILE_SELECT_SCALE,
    darken,
    grid_columns,
    mix,
    tile_face,
)

# --- Switch "Basic White" palette (single source of truth: vajsave.ui_theme) -------

BG = SWITCH["bg"]
SIDE = SWITCH["surface"]
SURFACE_ALT = SWITCH["surface_alt"]
CARD = SWITCH["card"]
TEXT = SWITCH["text"]
MUTED = SWITCH["muted"]
LINE = SWITCH["line"]
BLUE = SWITCH["accent"]
RING = SWITCH["ring"]
GREEN = SWITCH["success"]
ORANGE = SWITCH["warning"]
RED = SWITCH["danger"]
ON_ACCENT = SWITCH["on_accent"]

# Selected surfaces are a light-blue tint of the accent so white cards still read.
SELECTED_ROW = mix(BLUE, CARD, 0.84)
SELECTED_LIST = mix(BLUE, CARD, 0.86)


def open_in_file_manager(path: Union[Path, str]) -> tuple[bool, str]:
    target = Path(path)
    if not target.exists():
        return False, f"路径不存在: {target}"
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", str(target)], check=True)
        elif sys.platform == "win32":
            subprocess.run(["explorer", f"/select,{target}"], check=True)
        else:
            parent_dir = target if target.is_dir() else target.parent
            subprocess.run(["xdg-open", str(parent_dir)], check=True)
        return True, f"已在文件管理器中打开: {target}"
    except Exception as e:
        return False, f"打开失败: {e}"


def ui_font(size: int = 13, weight: str = "normal") -> tuple:
    family = "PingFang SC" if sys.platform == "darwin" else ("Microsoft YaHei" if sys.platform == "win32" else "Noto Sans CJK SC")
    return (family, size, weight) if weight != "normal" else (family, size)


def _rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    """Corner points for a smoothed polygon that approximates a rounded rect."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2,
        x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


class CanvasButton(tk.Canvas):
    """A flat rounded button drawn on a Canvas.

    Replaces every ``ttk`` button/checkbutton in the app so the Basic White
    surfaces never fall back to the platform's native chrome. Exposes the
    ``idle``/``hover``/``pressed`` interaction states, an ``accent`` variant, a
    ``selected`` toggle state, and an optional left status dot. The requested
    width is measured from the label font, and the height defaults to the
    bottom system bar's 34px with a 36px variant for the right action stack.
    """

    BOTTOM_HEIGHT = 34
    ACTION_HEIGHT = 36
    RADIUS = 9
    PADDING = 14
    DOT_SPAN = 18
    MIN_WIDTH = 48

    def __init__(
        self,
        master,
        text: str = "",
        command=None,
        *,
        variant: str = "neutral",
        accent: Optional[bool] = None,
        height: int = BOTTOM_HEIGHT,
        font=None,
        padding: int = PADDING,
        dot: Optional[str] = None,
        selected: bool = False,
        **kwargs,
    ) -> None:
        self._text = text
        self._command = command
        if accent is True:
            variant = "accent"
        self.variant = variant
        self.accent = variant == "accent"
        self.selected = bool(selected)
        self._interaction = "idle"
        self.enabled = True
        self._dot_color = dot
        self._font_spec = font or ui_font(12)
        self._padding = int(padding)
        self._height = int(height)
        self._font = tkfont.Font(root=master.winfo_toplevel(), font=self._font_spec)
        kwargs.setdefault("bg", self._parent_bg(master))
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        super().__init__(master, height=self._height, width=self._measure_width(), **kwargs)
        self.configure(takefocus=1)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self.invoke())
        self.bind("<space>", lambda _e: self.invoke())
        self.bind("<Configure>", lambda _e: self._render())
        self._render()

    # -- state ---------------------------------------------------------------

    @property
    def state(self) -> str:
        """One of ``idle``/``hover``/``pressed``/``selected``/``accent``.

        Pointer interaction wins over the persistent selected/accent styling so a
        hovered or pressed button always reports its transient state.
        """
        if self._interaction != "idle":
            return self._interaction
        if self.selected:
            return "selected"
        if self.accent:
            return "accent"
        return "idle"

    # -- configuration -------------------------------------------------------

    @staticmethod
    def _parent_bg(master) -> str:
        try:
            return str(master.cget("bg"))
        except Exception:  # noqa: BLE001 - fall back to the neutral bar surface
            return SWITCH["surface"]

    def _measure_width(self) -> int:
        width = self._font.measure(self._text) + self._padding * 2
        if self._dot_color:
            width += self.DOT_SPAN
        return max(self.MIN_WIDTH, int(width))

    def _current_width(self) -> int:
        width = self.winfo_width()
        if width <= 1:
            try:
                width = int(self.cget("width"))
            except (TypeError, ValueError):
                width = self._measure_width()
        return max(1, width)

    def _current_height(self) -> int:
        height = self.winfo_height()
        if height <= 1:
            height = self._height
        return max(1, height)

    def set_text(self, text: str) -> None:
        self._text = text
        self.configure(width=self._measure_width())
        self._render()

    def set_selected(self, selected: bool) -> None:
        selected = bool(selected)
        if selected == self.selected:
            return
        self.selected = selected
        self._render()

    def invoke(self):
        """Run the bound command exactly like a real click."""
        if not self.enabled or self._command is None:
            return None
        return self._command()

    # -- interactions --------------------------------------------------------

    def _on_enter(self, _event=None) -> None:
        self._interaction = "hover"
        self._render()

    def _on_leave(self, _event=None) -> None:
        self._interaction = "idle"
        self._render()

    def _on_press(self, _event=None) -> None:
        self.focus_set()
        self._interaction = "pressed"
        self._render()

    def _on_release(self, event=None) -> None:
        was_pressed = self._interaction == "pressed"
        self._interaction = "hover"
        self._render()
        if was_pressed and event is not None:
            inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
            if inside:
                self.invoke()

    # -- rendering -----------------------------------------------------------

    def _palette(self) -> Tuple[str, str, str]:
        colors = SWITCH
        interaction = self._interaction
        if self.accent:
            if interaction == "pressed":
                return darken(colors["accent"], 0.12), colors["on_accent"], ""
            if interaction == "hover":
                return colors["accent_hover"], colors["on_accent"], ""
            return colors["accent"], colors["on_accent"], ""
        if interaction == "pressed":
            return colors["line"], colors["text"], ""
        if interaction == "hover":
            return colors["hover"], colors["text"], ""
        if self.selected:
            return mix(colors["accent"], colors["card"], 0.84), colors["accent"], colors["ring"]
        return colors["surface_alt"], colors["text"], ""

    def _render(self) -> None:
        self.delete("all")
        width = self._current_width()
        height = self._current_height()
        fill, foreground, outline = self._palette()
        self.create_polygon(
            _rounded_points(1, 1, width - 1, height - 1, self.RADIUS),
            smooth=True,
            splinesteps=24,
            fill=fill,
            outline=outline,
            width=1,
            tags=("button-bg",),
        )
        text_x = width / 2
        if self._dot_color:
            center_x = self._padding + 4
            center_y = height / 2
            dot_fill = self._dot_color if self.selected else mix(self._dot_color, fill, 0.35)
            self.create_oval(
                center_x - 4,
                center_y - 4,
                center_x + 4,
                center_y + 4,
                fill=dot_fill,
                outline="",
                tags=("button-dot",),
            )
            text_x = (center_x + 4 + self.DOT_SPAN / 2 + width) / 2
        self.create_text(
            text_x,
            height / 2,
            text=self._text,
            fill=foreground,
            font=self._font_spec,
            tags=("button-text",),
        )


class SaveTileGrid(tk.Canvas):
    """A Switch HOME style grid of save tiles drawn on a Canvas.

    Behaves like ``tk.Listbox`` for the bits the app relies on
    (``curselection``/``selection_set``/``selection_clear``) but renders each
    save as a rounded white card. Click, Ctrl-click, Shift-click, the arrow
    keys, and double-click all drive the same selection model.
    """

    TILE_W = DEFAULT_TILE_WIDTH + 90  # roomier square-ish HOME tiles
    TILE_H = DEFAULT_TILE_WIDTH + 90
    GAP = DEFAULT_TILE_GAP
    PAD = 16

    def __init__(
        self,
        master,
        *,
        on_select=None,
        on_activate=None,
        colors: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> None:
        self.colors = colors or SWITCH
        kwargs.setdefault("bg", self.colors["bg"])
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("takefocus", 1)
        super().__init__(master, **kwargs)
        self._on_select = on_select
        self._on_activate = on_activate
        self._tiles: List[dict] = []
        self._boxes: List[Tuple[float, float, float, float]] = []
        self._selected: set = set()
        self._anchor: Optional[int] = None
        self._active: Optional[int] = None
        self._hover: Optional[int] = None
        self._cols = 1
        self._laying_out = False

        self.bind("<Enter>", self._on_pointer_enter)
        self.bind("<Leave>", self._on_pointer_leave)
        self.bind("<Motion>", self._on_pointer_motion)
        self.bind("<Button-1>", self._on_button)
        self.bind("<Control-Button-1>", lambda e: self._on_button(e, ctrl=True))
        self.bind("<Command-Button-1>", lambda e: self._on_button(e, ctrl=True))
        self.bind("<Shift-Button-1>", lambda e: self._on_button(e, shift=True))
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Left>", lambda e: self.move_active(-1, 0))
        self.bind("<Right>", lambda e: self.move_active(1, 0))
        self.bind("<Up>", lambda e: self.move_active(0, -1))
        self.bind("<Down>", lambda e: self.move_active(0, 1))
        self.bind("<Return>", lambda e: self._activate_active())
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)

    # -- model ---------------------------------------------------------------

    def set_tiles(self, tiles) -> None:
        self._tiles = list(tiles)
        self._selected = set()
        self._anchor = None
        self._active = None
        self._hover = None
        self._layout()

    def size(self) -> int:
        return len(self._tiles)

    def columns(self) -> int:
        return self._cols

    @property
    def hover_index(self) -> Optional[int]:
        return self._hover

    def set_hover(self, index: Optional[int]) -> None:
        """Highlight the tile under the pointer (``None`` clears it)."""
        if index is not None and not (0 <= index < len(self._tiles)):
            index = None
        if index == self._hover:
            return
        self._hover = index
        self._layout()

    def curselection(self) -> Tuple[int, ...]:
        return tuple(sorted(self._selected))

    def selection_set(self, first: int, last: Optional[int] = None) -> None:
        if last is None:
            self._selected = {first}
        else:
            self._selected = set(range(first, last + 1))
        self._anchor = first
        self._active = last if last is not None else first
        self._layout()

    def selection_clear(self, _first=None, _last=None) -> None:
        self._selected = set()
        self._layout()

    def select_index(
        self,
        index: int,
        *,
        extend: bool = False,
        toggle: bool = False,
        notify: bool = True,
    ) -> None:
        if not (0 <= index < len(self._tiles)):
            return
        if extend and self._anchor is not None:
            low, high = sorted((self._anchor, index))
            self._selected = set(range(low, high + 1))
        elif toggle:
            if index in self._selected:
                self._selected.discard(index)
            else:
                self._selected.add(index)
            self._anchor = index
        else:
            self._selected = {index}
            self._anchor = index
        self._active = index
        self._layout()
        self.scroll_into_view(index)
        if notify and self._on_select is not None:
            self._on_select(index)

    def move_active(self, dx: int, dy: int) -> str:
        total = len(self._tiles)
        if total == 0:
            return "break"
        if self._active is None:
            target = 0
        elif dy:
            target = self._active + dy * self._cols
        else:
            target = self._active + dx
        target = max(0, min(total - 1, target))
        self.select_index(target)
        return "break"

    def activate_index(self, index: int) -> None:
        if not (0 <= index < len(self._tiles)):
            return
        self.select_index(index)
        if self._on_activate is not None:
            self._on_activate(index)

    def scroll_into_view(self, index: int) -> None:
        if not (0 <= index < len(self._boxes)):
            return
        _, y1, _, y2 = self._boxes[index]
        region = self.bbox("all")
        if not region or region[3] <= 0:
            return
        total = region[3]
        view_h = self._viewport_height()
        top = self.canvasy(0)
        bottom = top + view_h
        if y1 < top:
            self.yview_moveto(max(0.0, y1 / total))
        elif y2 > bottom:
            self.yview_moveto(min(1.0, max(0.0, (y2 - view_h) / total)))

    # -- events --------------------------------------------------------------

    def _on_pointer_enter(self, event) -> str:
        self.set_hover(self._index_at(event.x, event.y))
        return "break"

    def _on_pointer_leave(self, _event=None) -> str:
        self.set_hover(None)
        return "break"

    def _on_pointer_motion(self, event) -> str:
        self.set_hover(self._index_at(event.x, event.y))
        return "break"

    def _on_button(self, event, ctrl: bool = False, shift: bool = False) -> str:
        self.focus_set()
        index = self._index_at(event.x, event.y)
        if index is None:
            self.selection_clear()
            return "break"
        self.select_index(index, extend=shift, toggle=ctrl)
        return "break"

    def _on_double(self, event) -> str:
        index = self._index_at(event.x, event.y)
        if index is not None:
            self.activate_index(index)
        return "break"

    def _activate_active(self) -> str:
        if self._active is not None:
            self.activate_index(self._active)
        return "break"

    def _on_wheel(self, event) -> str:
        num = getattr(event, "num", None)
        if num:
            delta = -1 if num == 4 else 1
        elif sys.platform == "darwin":
            delta = -1 * int(event.delta)
        else:
            delta = -1 * int(event.delta / 120)
        self.yview_scroll(delta, "units")
        return "break"

    def _index_at(self, x: float, y: float) -> Optional[int]:
        cx, cy = self.canvasx(x), self.canvasy(y)
        for i, (x1, y1, x2, y2) in enumerate(self._boxes):
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return i
        return None

    # -- rendering -----------------------------------------------------------

    def _canvas_width(self) -> int:
        width = self.winfo_width()
        if width <= 1:
            try:
                width = int(self.cget("width"))
            except (TypeError, ValueError):
                width = self.TILE_W
        return max(1, width)

    def _viewport_height(self) -> int:
        height = self.winfo_height()
        if height <= 1:
            try:
                height = int(self.cget("height"))
            except (TypeError, ValueError):
                height = self.TILE_H
        return max(1, height)

    def _layout(self) -> None:
        if self._laying_out:
            return
        self._laying_out = True
        try:
            self.delete("all")
            self._boxes = []
            total = len(self._tiles)
            width = self._canvas_width()
            if total == 0:
                self.create_text(
                    width / 2,
                    self.TILE_H,
                    text="没有可显示的存档",
                    fill=self.colors["muted"],
                    font=ui_font(13),
                )
                self.configure(scrollregion=(0, 0, width, self.TILE_H))
                return
            self._cols = grid_columns(width, self.TILE_W, self.GAP)
            rows = (total + self._cols - 1) // self._cols
            for i, face in enumerate(self._tiles):
                row, col = divmod(i, self._cols)
                x1 = self.PAD + col * (self.TILE_W + self.GAP)
                y1 = self.PAD + row * (self.TILE_H + self.GAP)
                x2 = x1 + self.TILE_W
                y2 = y1 + self.TILE_H
                self._draw_tile(i, face, x1, y1, x2, y2)
                self._boxes.append((x1, y1, x2, y2))
            content_h = self.PAD * 2 + rows * self.TILE_H + (rows - 1) * self.GAP
            self.configure(scrollregion=(0, 0, width, content_h))
        finally:
            self._laying_out = False

    def _draw_tile(self, index: int, face: dict, x1: float, y1: float, x2: float, y2: float) -> None:
        colors = self.colors
        selected = index in self._selected
        hovered = index == self._hover

        # The selected tile grows outward; the base box stays for hit testing.
        if selected:
            grow = TILE_SELECT_SCALE / 2
            x1, y1, x2, y2 = x1 - grow, y1 - grow, x2 + grow, y2 + grow
            gap = TILE_RING_GAP
            self.create_polygon(
                _rounded_points(x1 - gap, y1 - gap, x2 + gap, y2 + gap, TILE_RADIUS + gap),
                smooth=True,
                splinesteps=24,
                fill="",
                outline=colors["ring"],
                width=TILE_RING_WIDTH,
                tags=("tile-ring", f"ring{index}"),
            )

        if hovered and not selected:
            fill = colors["hover"]
            outline = colors["line"]
        else:
            fill = face.get("face", colors["card"])
            outline = ""
        self.create_polygon(
            _rounded_points(x1, y1, x2, y2, TILE_RADIUS),
            smooth=True,
            splinesteps=24,
            fill=fill,
            outline=outline,
            width=1,
            tags=("tile", f"tile{index}"),
        )

        accent = face.get("accent", colors["accent"])
        center = (x1 + x2) / 2
        span = y2 - y1

        # Full-width platform colour bar hugging the bottom edge.
        self.create_rectangle(
            x1,
            y2 - TILE_BAR_HEIGHT,
            x2,
            y2,
            fill=accent,
            outline="",
            tags=("tile-bar", f"bar{index}"),
        )

        # Big centred monogram on the pastel face.
        self.create_text(
            center,
            y1 + span * 0.40,
            text=face.get("monogram", "?"),
            fill=accent,
            font=ui_font(30, "bold"),
            tags=("tile-mono", f"mono{index}"),
        )
        if face.get("starred"):
            self.create_text(
                x2 - 22,
                y1 + 24,
                text="★",
                fill=colors["star"],
                font=ui_font(14, "bold"),
                tags=("tile-star", f"star{index}"),
            )

        self.create_text(
            center,
            y1 + span * 0.62,
            text=face.get("title", ""),
            fill=colors["text"],
            font=ui_font(13, "bold" if selected else "normal"),
            width=self.TILE_W - 26,
            anchor="n",
            justify="center",
            tags=("tile-title", f"title{index}"),
        )
        if face.get("subtitle"):
            self.create_text(
                center,
                y2 - 58,
                text=face["subtitle"],
                fill=colors["muted"],
                font=ui_font(11),
                width=self.TILE_W - 26,
                anchor="n",
                justify="center",
                tags=("tile-sub", f"sub{index}"),
            )

        pill = face.get("pill") or {}
        label = pill.get("label", "")
        if label:
            pill_w = min(self.TILE_W - 30, 18 + 13 * len(label))
            px1, px2 = center - pill_w / 2, center + pill_w / 2
            py1, py2 = y2 - 36, y2 - 14
            self.create_polygon(
                _rounded_points(px1, py1, px2, py2, (py2 - py1) / 2),
                smooth=True,
                splinesteps=24,
                fill=pill.get("bg", colors["surface_alt"]),
                outline="",
                tags=("tile-pill", f"pill{index}"),
            )
            self.create_text(
                center,
                (py1 + py2) / 2,
                text=label,
                fill=pill.get("fg", colors["muted"]),
                font=ui_font(11, "bold"),
                tags=("tile-pill-label", f"pilltext{index}"),
            )


class VajSaveApp:
    def __init__(self, root: Optional[tk.Tk] = None, state: Optional[AppState] = None) -> None:
        self.root = root or tk.Tk()
        self.state = state or AppState()
        self._selected_save: Optional[SaveEntry] = None
        self._selected_snapshot: Optional[Snapshot] = None
        self._saves_index: List[SaveEntry] = []
        self._volumes_index: List[VolumeInfo] = []
        self._versions_index: List[Snapshot] = []
        self._platform_rows: Dict[str, dict] = {}
        self._watch_var = tk.BooleanVar(value=True)
        self._hide_unchanged_var = tk.BooleanVar(value=True)
        self._poll_interval_ms = 200
        self._clock_interval_ms = 30000
        # Enough for the first paint to land before the opening scan starts.
        self._initial_select_delay_ms = 60

        self._init_window()
        self._apply_theme()
        self._create_widgets()
        self._bind_events()
        # Enumerate right away so the device list is populated on first paint, but
        # defer the first scan: a full SD card can take seconds to walk, and running
        # it inside __init__ would delay the window from ever appearing.
        self.state.refresh_volumes()
        self.refresh_volumes_ui()
        self.refresh_platform_ui()
        self.refresh_saves_ui()
        self.refresh_stats()
        self._initial_select_job = self.root.after(self._initial_select_delay_ms, self._initial_auto_select)
        self._clock_job = self.root.after(self._clock_interval_ms, self._tick_clock)
        if self._watch_var.get():
            self.state.start_watch()
        self._poll_job = self.root.after(self._poll_interval_ms, self._poll_events)

    def _initial_auto_select(self) -> None:
        """Choose a default device once the window is up, then scan it.

        Idempotent: if the volume watcher already auto-selected something, this is a
        no-op, so the startup burst and this deferred call cannot double-scan.
        """
        self._initial_select_job = None
        if self.state.ensure_mount_selected() is None:
            return
        self.refresh_volumes_ui(select_path=self.state.current_mount)
        self.refresh_platform_ui()
        self.refresh_saves_ui()
        self.refresh_stats()

    def _init_window(self) -> None:
        self.root.title("vaj-save")
        self.root.minsize(1080, 680)
        self.root.geometry("1180x740")
        self.root.configure(bg=BG)

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=TEXT, font=ui_font(13))
        # Buttons/checkbuttons are drawn by ``CanvasButton``; only the inputs and
        # scrollbars still rely on ttk chrome.
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, bordercolor=LINE, padding=6)
        style.configure(
            "Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            borderwidth=0,
            rowheight=32,
            font=ui_font(13),
        )
        style.configure("Treeview.Heading", background=SURFACE_ALT, foreground=MUTED, relief="flat", font=ui_font(12))
        style.map("Treeview", background=[("selected", BLUE)], foreground=[("selected", ON_ACCENT)])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _create_widgets(self) -> None:
        # Top status bar: app identity on the left, live clock + stats on the right.
        topbar = tk.Frame(self.root, bg=SIDE, height=64)
        topbar.pack(fill=tk.X)
        topbar.pack_propagate(False)

        brand = tk.Frame(topbar, bg=SIDE)
        brand.pack(side=tk.LEFT, padx=20, pady=12)
        badge = tk.Canvas(brand, width=36, height=36, bg=SIDE, highlightthickness=0, bd=0)
        badge.pack(side=tk.LEFT)
        badge.create_oval(1, 1, 35, 35, fill=BLUE, outline="")
        badge.create_text(18, 18, text="v", fill=ON_ACCENT, font=ui_font(16, "bold"))
        titles = tk.Frame(brand, bg=SIDE)
        titles.pack(side=tk.LEFT, padx=10)
        tk.Label(titles, text="vaj-save", bg=SIDE, fg=TEXT, font=ui_font(17, "bold")).pack(anchor="w")
        tk.Label(titles, text="把掌机存档备份下来，按版本管理", bg=SIDE, fg=MUTED, font=ui_font(11)).pack(anchor="w")

        status_right = tk.Frame(topbar, bg=SIDE)
        status_right.pack(side=tk.RIGHT, padx=20, pady=12)
        self.clock_var = tk.StringVar(value=time.strftime("%H:%M"))
        tk.Label(status_right, textvariable=self.clock_var, bg=SIDE, fg=TEXT, font=ui_font(16, "bold")).pack(anchor="e")
        self.stats_var = tk.StringVar(value="")
        tk.Label(status_right, textvariable=self.stats_var, bg=SIDE, fg=MUTED, font=ui_font(11)).pack(anchor="e")

        toolbar = tk.Frame(self.root, bg=BG)
        toolbar.pack(fill=tk.X, padx=24, pady=(12, 10))
        tk.Label(toolbar, text="搜索", bg=BG, fg=MUTED, font=ui_font(12)).pack(side=tk.LEFT, padx=(0, 8))
        self.search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search.bind("<KeyRelease>", self.on_search)

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 8))

        left = tk.Frame(body, bg=SIDE, width=220)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        tk.Label(left, text="机种", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(16, 8))
        self.platform_box = tk.Frame(left, bg=SIDE)
        self.platform_box.pack(fill=tk.X, padx=8)
        tk.Label(left, text="设备", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(18, 8))
        self.vol_list = tk.Listbox(
            left,
            bg=CARD,
            fg=TEXT,
            selectbackground=BLUE,
            selectforeground=ON_ACCENT,
            font=ui_font(12),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
        )
        self.vol_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 16))

        mid = tk.Frame(body, bg=BG)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=14)
        tk.Label(mid, text="游戏", bg=BG, fg=MUTED, font=ui_font(12)).pack(anchor="w", pady=(0, 8))
        grid_shell = tk.Frame(mid, bg=BG)
        grid_shell.pack(fill=tk.BOTH, expand=True)
        self.save_list = SaveTileGrid(
            grid_shell,
            on_select=self.on_save_selected,
            on_activate=self.on_tile_activated,
        )
        save_scroll = ttk.Scrollbar(grid_shell, orient=tk.VERTICAL, command=self.save_list.yview)
        self.save_list.configure(yscrollcommand=save_scroll.set)
        save_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.save_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right = tk.Frame(body, bg=SIDE, width=340)
        right.pack(side=tk.LEFT, fill=tk.Y)
        right.pack_propagate(False)
        tk.Label(right, text="详情", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(16, 8))

        # Action buttons are packed to the BOTTOM first so a long detail text or
        # a tiny window can never push them off-screen; the scrollable detail
        # region below then fills whatever space is left.
        actions = tk.Frame(right, bg=SIDE)
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=12, pady=16)
        self.actions_frame = actions
        self._action_buttons: List[CanvasButton] = []

        def _action_button(text: str, command, *, accent: bool = False) -> CanvasButton:
            button = CanvasButton(
                actions,
                text=text,
                command=command,
                variant="accent" if accent else "neutral",
                height=CanvasButton.ACTION_HEIGHT,
            )
            button.pack(fill=tk.X, pady=3)
            self._action_buttons.append(button)
            return button

        _action_button("备份", self.on_save_local_clicked, accent=True)
        _action_button("备份所选", self.on_save_selected_clicked)
        _action_button("备份当前列表", self.on_save_visible_clicked)
        _action_button("恢复这个版本…", self.on_restore_clicked)
        _action_button("导出 ZIP", self.on_export_zip_clicked)
        _action_button("收藏", self.on_star_clicked)
        finder = "在访达中显示" if sys.platform == "darwin" else "在文件管理器中显示"
        _action_button(finder, self.on_show_in_finder_clicked)
        _action_button("只看收藏", self.on_starred_filter)

        # Vertically scrollable detail region (detail text + versions + note).
        scroll_shell = tk.Frame(right, bg=SIDE)
        scroll_shell.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.detail_canvas = tk.Canvas(scroll_shell, bg=SIDE, highlightthickness=0, bd=0)
        detail_scroll = ttk.Scrollbar(scroll_shell, orient=tk.VERTICAL, command=self.detail_canvas.yview)
        self.detail_canvas.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_inner = tk.Frame(self.detail_canvas, bg=SIDE)
        self.detail_inner = detail_inner
        self._detail_window = self.detail_canvas.create_window((0, 0), window=detail_inner, anchor="nw")

        def _on_inner_configure(_event=None):
            self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all"))

        def _on_canvas_configure(event):
            self.detail_canvas.itemconfigure(self._detail_window, width=event.width)

        detail_inner.bind("<Configure>", _on_inner_configure)
        self.detail_canvas.bind("<Configure>", _on_canvas_configure)

        self.detail_name = tk.StringVar(value="未选择游戏")
        self.detail_meta = tk.StringVar(value="从中间列表点选一条存档")
        tk.Label(detail_inner, textvariable=self.detail_name, bg=SIDE, fg=TEXT, font=ui_font(16, "bold"), wraplength=280, justify="left").pack(anchor="w", padx=16)
        tk.Label(detail_inner, textvariable=self.detail_meta, bg=SIDE, fg=MUTED, font=ui_font(12), wraplength=280, justify="left").pack(anchor="w", padx=16, pady=(4, 12))

        tk.Label(detail_inner, text="版本", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(4, 6))
        self.version_list = tk.Listbox(
            detail_inner,
            bg=CARD,
            fg=TEXT,
            selectbackground=BLUE,
            selectforeground=ON_ACCENT,
            font=ui_font(12),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            height=8,
        )
        self.version_list.pack(fill=tk.X, padx=12)

        tk.Label(detail_inner, text="备注", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(12, 6))
        self.note_var = tk.StringVar()
        note = ttk.Entry(detail_inner, textvariable=self.note_var)
        note.pack(fill=tk.X, padx=12, pady=(0, 12))
        note.bind("<FocusOut>", self.on_note_commit)
        note.bind("<Return>", self.on_note_commit)

        self._bind_detail_scroll(self.detail_canvas)
        self._bind_detail_scroll(detail_inner)

        self.path_entry_var = tk.StringVar(value="")
        path = ttk.Entry(self.root, textvariable=self.path_entry_var)
        path.pack(fill=tk.X, padx=24, pady=(0, 8))

        # Bottom system bar: quick actions + monitoring toggles.
        bottombar = tk.Frame(self.root, bg=SIDE)
        bottombar.pack(fill=tk.X, padx=24, pady=(0, 8))
        self._bottombar = bottombar
        self._bottom_buttons: List[CanvasButton] = []

        def _bar_button(text: str, command, padx, *, dot: Optional[str] = None) -> CanvasButton:
            button = CanvasButton(
                bottombar,
                text=text,
                command=command,
                height=CanvasButton.BOTTOM_HEIGHT,
                dot=dot,
            )
            button.pack(side=tk.LEFT, padx=padx, pady=8)
            self._bottom_buttons.append(button)
            return button

        _bar_button("刷新", self.on_refresh_clicked, (12, 6))
        _bar_button("打开文件夹", self.on_open_folder_clicked, (6, 6))
        _bar_button("打开本地库", self.on_open_library_clicked, (6, 6))
        _bar_button("设置", self.on_settings_clicked, (6, 6))
        self._watch_button = _bar_button("监听插拔", self.on_watch_button_clicked, (12, 0), dot=BLUE)
        self._watch_button.set_selected(bool(self._watch_var.get()))
        self._hide_unchanged_var.set(bool(self.state.hide_unchanged))
        self._hide_unchanged_button = _bar_button(
            "隐藏已备份", self.on_hide_unchanged_button_clicked, (12, 0), dot=GREEN
        )
        self._hide_unchanged_button.set_selected(bool(self._hide_unchanged_var.get()))

        statusrow = tk.Frame(self.root, bg=BG)
        statusrow.pack(fill=tk.X, padx=24, pady=(0, 14))
        self.status_label_var = tk.StringVar(value="准备好了，插上掌机或打开文件夹就可以开始")
        self.status_label = tk.Label(statusrow, textvariable=self.status_label_var, bg=BG, fg=MUTED, anchor="w", font=ui_font(12))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.warning_label_var = tk.StringVar(value="")
        self.warning_label = tk.Label(statusrow, textvariable=self.warning_label_var, bg=BG, fg=RED, anchor="e", font=ui_font(12))
        self.warning_label.pack(side=tk.RIGHT)

        self.vol_tree = self.vol_list
        self.save_tree = self.save_list
        self.version_tree = self.version_list
        self._build_platform_rows()

    def _build_platform_rows(self) -> None:
        """Create the platform filter rows exactly once, then keep updating them.

        Rebuilding on every refresh churned widget ids and made the left panel
        flicker; the static set of platform keys never changes, so the rows are
        only created on the first call. ``_update_platform_rows`` re-colours the
        rows and rewrites the count badges in place.
        """
        if self._platform_rows:
            self._update_platform_rows()
            return
        for key in PLATFORM_ORDER:
            row = tk.Frame(self.platform_box, bg=SIDE, cursor="hand2")
            row.pack(fill=tk.X, pady=1)
            pip = tk.Frame(row, bg=PLATFORM_COLORS.get(key, BLUE), width=3)
            pip.pack(side=tk.LEFT, fill=tk.Y)
            name = tk.Label(row, text=PLATFORM_LABELS.get(key, key), bg=SIDE, fg=TEXT, font=ui_font(13), anchor="w")
            name.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)
            badge = tk.Label(row, text="0", bg=SIDE, fg=MUTED, font=ui_font(12))
            badge.pack(side=tk.RIGHT, padx=12)
            for widget in (row, pip, name, badge):
                widget.bind("<Button-1>", lambda _e, platform=key: self.on_platform_clicked(platform))
            self._platform_rows[key] = {"row": row, "name": name, "badge": badge, "pip": pip}
        self._update_platform_rows()

    def _update_platform_rows(self) -> None:
        counts = self.state.platform_counts()
        total = len(self.state.all_saves())
        for key in PLATFORM_ORDER:
            widgets = self._platform_rows.get(key)
            if widgets is None:
                continue
            count = total if key == "all" else counts.get(key, 0)
            selected = key == self.state.selected_platform
            row_bg = SELECTED_ROW if selected else SIDE
            widgets["row"].configure(bg=row_bg)
            widgets["name"].configure(bg=row_bg)
            widgets["badge"].configure(bg=row_bg, text=str(count))
            widgets["pip"].configure(bg=PLATFORM_COLORS.get(key, BLUE))

    def _bind_events(self) -> None:
        self.vol_list.bind("<<ListboxSelect>>", self.on_volume_selected)
        self.version_list.bind("<<ListboxSelect>>", self.on_version_selected)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _tick_clock(self) -> None:
        self.clock_var.set(time.strftime("%H:%M"))
        self._clock_job = self.root.after(self._clock_interval_ms, self._tick_clock)

    def on_platform_clicked(self, platform: str) -> None:
        self.state.set_platform_filter(platform)
        self.refresh_platform_ui()
        self.refresh_saves_ui()

    def on_search(self, _event=None) -> None:
        self.state.set_search_query(self.search_var.get())
        self.refresh_saves_ui()

    def on_starred_filter(self) -> None:
        self.state.toggle_starred_only()
        self.refresh_saves_ui()
        self.update_status("正在只看收藏" if self.state.starred_only else "已显示全部游戏")

    def on_hide_unchanged_button_clicked(self) -> None:
        """CanvasButton for "隐藏已备份": flip the flag, then run the shared handler."""
        self._hide_unchanged_var.set(not self._hide_unchanged_var.get())
        self.on_hide_unchanged_toggle()

    def on_hide_unchanged_toggle(self) -> None:
        desired = bool(self._hide_unchanged_var.get())
        if desired != self.state.hide_unchanged:
            self.state.toggle_hide_unchanged()
        self._hide_unchanged_var.set(self.state.hide_unchanged)
        button = getattr(self, "_hide_unchanged_button", None)
        if button is not None:
            button.set_selected(bool(self._hide_unchanged_var.get()))
        self.refresh_saves_ui()
        self.update_status("已隐藏已备份" if self.state.hide_unchanged else "显示已备份")

    def on_refresh_clicked(self) -> None:
        self.state.refresh_volumes()
        self.state.ensure_mount_selected()
        self.refresh_volumes_ui(select_path=self.state.current_mount)
        self.refresh_platform_ui()
        self.refresh_saves_ui()
        self.refresh_stats()

    def on_open_folder_clicked(self) -> None:
        chosen_dir = filedialog.askdirectory(title="选择要扫描的文件夹")
        if chosen_dir:
            self.state.select_custom_path(chosen_dir)
            self.refresh_volumes_ui(select_path=Path(chosen_dir))
            self.refresh_platform_ui()
            self.refresh_saves_ui()
            self.refresh_stats()

    def on_watch_button_clicked(self) -> None:
        """CanvasButton for "监听插拔": flip the flag, then run the shared handler."""
        self._watch_var.set(not self._watch_var.get())
        self.on_watch_toggle()

    def on_watch_toggle(self) -> None:
        if self._watch_var.get():
            self.state.start_watch()
            self.update_status("已开始监听设备插拔")
        else:
            self.state.stop_watch()
            self.update_status("已停止监听")
        button = getattr(self, "_watch_button", None)
        if button is not None:
            button.set_selected(bool(self._watch_var.get()))

    def on_volume_selected(self, event=None) -> None:
        selection = self.vol_list.curselection()
        if not selection:
            return
        index = selection[0]
        if index >= len(self._volumes_index):
            return
        # Index mapping avoids mis-parsing volume names that contain the label
        # separator ("  ·  ") used to render the listbox row.
        volume = self._volumes_index[index]
        if self.state.current_mount == volume.mount_point and self.state.current_result is not None:
            # Re-selecting the active device keeps its scan; "刷新" forces a rescan.
            return
        self.state.select_mount(volume.mount_point)
        self.refresh_platform_ui()
        self.refresh_saves_ui()

    def on_save_selected(self, event=None) -> None:
        selection = self.save_list.curselection()
        if not selection or selection[-1] >= len(self._saves_index):
            self._selected_save = None
            self.path_entry_var.set("")
            self.detail_name.set("未选择游戏")
            self.detail_meta.set("从中间列表点选一条存档")
            self.refresh_versions_ui()
            return
        # Primary detail follows the active (last) selection in multi-select.
        index = selection[-1]
        save = self._saves_index[index]
        self._selected_save = save
        self.path_entry_var.set(save.path)
        star = "已收藏 · " if self.state.is_starred(save) else ""
        status = self.state.save_status(save)
        status_label = BACKUP_STATUS_LABELS.get(status.status, status.status)
        lines = [
            f"{star}{PLATFORM_LABELS.get(save.platform, save.platform)}  {save.title_id or ''}  {save.slot or ''}".rstrip(),
            f"状态: {status_label}",
        ]
        if status.source_mtime:
            lines.append(f"卡上时间: {status.source_mtime}")
        if status.last_backup_at:
            lines.append(f"上次备份: {status.last_backup_at}")
        if status.mtime_stale:
            lines.append("卡上时间早于上次备份（可能是回档或拷贝）")
        self.detail_name.set(save.display_name)
        self.detail_meta.set("\n".join(lines))
        self.note_var.set(self.state.game_note(save))
        self.refresh_versions_ui()

    def on_tile_activated(self, index: int) -> None:
        """Double-clicking a tile runs the primary backup action for that save."""
        if 0 <= index < len(self._saves_index):
            self._selected_save = self._saves_index[index]
        self.on_save_local_clicked()

    def on_version_selected(self, event=None) -> None:
        selection = self.version_list.curselection()
        if not selection or selection[0] >= len(self._versions_index):
            self._selected_snapshot = None
            return
        self._selected_snapshot = self._versions_index[selection[0]]

    def on_show_in_finder_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("先选一条存档")
            return
        ok, msg = open_in_file_manager(self._selected_save.path)
        self.update_status(msg if ok else self.state.status_text)
        self.update_warning("" if ok else msg)

    def on_copy_path_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("先选一条存档")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._selected_save.path)
        self.update_status("路径已复制")

    def on_save_local_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("先选一条存档")
            return
        dest = self.state.import_save(self._selected_save)
        self.update_status(self.state.status_text)
        self.refresh_saves_ui()
        self.refresh_versions_ui()
        self.refresh_stats()
        self.update_warning("" if dest else "备份失败")

    def _selected_saves(self) -> List[SaveEntry]:
        selected: List[SaveEntry] = []
        for index in self.save_list.curselection():
            if 0 <= index < len(self._saves_index):
                selected.append(self._saves_index[index])
        return selected

    def on_save_selected_clicked(self) -> None:
        chosen = self._selected_saves()
        if not chosen and self._selected_save is not None:
            chosen = [self._selected_save]
        if not chosen:
            self.update_warning("先选一条或多条存档")
            return
        copied = self.state.import_selected_saves(chosen)
        self.update_status(self.state.status_text)
        self.refresh_saves_ui()
        self.refresh_versions_ui()
        self.refresh_stats()
        self.update_warning("" if copied else "备份失败")

    def on_save_visible_clicked(self) -> None:
        copied = self.state.import_visible_saves()
        self.update_status(self.state.status_text)
        self.refresh_saves_ui()
        self.refresh_versions_ui()
        self.refresh_stats()
        self.update_warning("" if copied else "当前列表没有可备份的存档")

    def on_restore_clicked(self) -> None:
        if not self._selected_snapshot:
            self.update_warning("先在右侧选一个版本")
            return
        chosen = filedialog.askdirectory(title="恢复到哪个文件夹？")
        if not chosen:
            return
        restored = self.state.restore_version(self._selected_snapshot, chosen)
        self.update_status(self.state.status_text)
        self.update_warning("" if restored else "恢复失败")

    def on_export_zip_clicked(self) -> None:
        if not self._selected_snapshot:
            self.update_warning("先在右侧选一个版本")
            return
        dest = filedialog.asksaveasfilename(title="导出 ZIP", defaultextension=".zip", filetypes=[("Zip", "*.zip")])
        if not dest:
            return
        exported = self.state.export_version_zip(self._selected_snapshot, dest)
        self.update_status(self.state.status_text)
        self.update_warning("" if exported else "导出失败")

    def on_star_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("先选一条存档")
            return
        self.state.toggle_star(self._selected_save)
        self.update_status(self.state.status_text)
        self.refresh_saves_ui()
        self.refresh_stats()

    def on_note_commit(self, _event=None) -> None:
        if not self._selected_save:
            return
        self.state.set_note(self._selected_save, self.note_var.get())
        self.update_status("备注已保存")

    def on_open_library_clicked(self) -> None:
        self.state.library_root.mkdir(parents=True, exist_ok=True)
        ok, msg = open_in_file_manager(self.state.library_root)
        self.update_status(f"本地库：{self.state.library_root}" if ok else msg)
        self.update_warning("" if ok else msg)

    def _apply_library_root(self, path: str) -> None:
        """Apply a new library root chosen in the settings dialog."""
        self.state.set_library_root(path)
        self.refresh_saves_ui()
        self.refresh_stats()
        self.update_status(f"备份库已切换到 {self.state.library_root}")
        self.update_warning("")

    def on_settings_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        tk.Label(dialog, text="本地备份库路径", bg=BG, fg=TEXT, font=ui_font(13, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
        path_var = tk.StringVar(value=str(self.state.library_root))
        entry = ttk.Entry(dialog, textvariable=path_var, width=52)
        entry.pack(fill=tk.X, padx=16)

        def browse() -> None:
            chosen = filedialog.askdirectory(
                title="选择备份库目录",
                parent=dialog,
                initialdir=path_var.get() or None,
            )
            if chosen:
                path_var.set(chosen)

        def save() -> None:
            chosen = path_var.get().strip()
            if not chosen:
                return
            dialog.destroy()
            self._apply_library_root(chosen)

        def cancel() -> None:
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=BG)
        btn_row.pack(fill=tk.X, padx=16, pady=16)
        CanvasButton(btn_row, text="浏览…", command=browse).pack(side=tk.LEFT)
        CanvasButton(btn_row, text="取消", command=cancel).pack(side=tk.RIGHT)
        CanvasButton(btn_row, text="保存", variant="accent", command=save).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.grab_set()
        entry.focus_set()

    def _bind_detail_scroll(self, widget) -> None:
        """Route wheel events in the detail region to the surrounding canvas.

        Widgets that already scroll themselves (the versions Listbox, the note
        Entry) keep their native wheel handling: an instance binding returning
        "break" would shadow Tk's class binding and make the outer canvas move
        instead of the widget under the pointer.
        """
        if not self._widget_handles_wheel(widget):
            widget.bind("<MouseWheel>", self._on_detail_mousewheel)
            widget.bind("<Button-4>", self._on_detail_mousewheel_linux)
            widget.bind("<Button-5>", self._on_detail_mousewheel_linux)
        for child in widget.winfo_children():
            self._bind_detail_scroll(child)

    @staticmethod
    def _widget_handles_wheel(widget) -> bool:
        """True for widgets whose class binding already implements wheel scrolling."""
        wheel_aware = (tk.Listbox, tk.Text, tk.Entry, tk.Spinbox, ttk.Entry, ttk.Combobox)
        spinbox = getattr(ttk, "Spinbox", None)
        if spinbox is not None:
            wheel_aware = wheel_aware + (spinbox,)
        return isinstance(widget, wheel_aware)

    def _on_detail_mousewheel(self, event) -> str:
        if sys.platform == "darwin":
            delta = -1 * event.delta
        else:
            delta = -1 * int(event.delta / 120)
        self.detail_canvas.yview_scroll(delta, "units")
        return "break"

    def _on_detail_mousewheel_linux(self, event) -> str:
        self.detail_canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
        return "break"

    def _poll_events(self) -> None:
        drained = self.state.drain_events()
        if drained > 0:
            self.refresh_volumes_ui()
            self.refresh_platform_ui()
            self.refresh_saves_ui()
            self.refresh_stats()
        self._poll_job = self.root.after(self._poll_interval_ms, self._poll_events)

    def refresh_platform_ui(self) -> None:
        self._build_platform_rows()
        self.update_status(self.state.status_text)
        self.update_warning("; ".join(self.state.warnings) if self.state.warnings else "")

    def refresh_volumes_ui(self, select_path: Optional[Path] = None) -> None:
        self.vol_list.delete(0, tk.END)
        self._volumes_index = []
        target = select_path or self.state.current_mount
        selected = None
        for index, vol in enumerate(self.state.volumes):
            self.vol_list.insert(tk.END, f"{vol.name}  ·  {vol.mount_point}")
            self._volumes_index.append(vol)
            if target and Path(vol.mount_point) == Path(target):
                selected = index
        if selected is not None:
            self.vol_list.selection_set(selected)
        self.update_status(self.state.status_text)

    def refresh_saves_ui(self) -> None:
        previous = self._selected_save
        self._selected_save = None
        self._saves_index = []
        faces: List[dict] = []
        restore_index: Optional[int] = None
        for _group_name, saves in self.state.grouped_saves():
            for save in saves:
                status = self.state.save_status(save)
                faces.append(tile_face(save, status, starred=self.state.is_starred(save)))
                self._saves_index.append(save)
                if previous and previous.path == save.path:
                    restore_index = len(self._saves_index) - 1
        self.save_list.set_tiles(faces)
        if restore_index is not None:
            self._selected_save = self._saves_index[restore_index]
            self.save_list.select_index(restore_index, notify=False)
            self.on_save_selected()
        else:
            self.path_entry_var.set("")
            self.detail_name.set("未选择游戏")
            self.detail_meta.set("从中间列表点选一条存档")
            self.refresh_versions_ui()
        self.update_status(self.state.status_text)

    def refresh_versions_ui(self) -> None:
        self.version_list.delete(0, tk.END)
        self._versions_index = []
        self._selected_snapshot = None
        if not self._selected_save:
            return
        snapshots = list(reversed(self.state.versions_for_entry(self._selected_save)))
        for snap in snapshots:
            self.version_list.insert(tk.END, f"{snap.created_at}    {snap.id}")
            self._versions_index.append(snap)
        if snapshots:
            self.version_list.selection_set(0)
            self._selected_snapshot = self._versions_index[0]

    def refresh_stats(self) -> None:
        stats = self.state.collection_stats()
        self.stats_var.set(f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本")

    def update_status(self, text: str) -> None:
        self.status_label_var.set(text)

    def update_warning(self, text: str) -> None:
        self.warning_label_var.set(text)

    def _stop_background(self) -> None:
        """Cancel background jobs and stop the volume watcher, leaving the window alone.

        Split out from ``on_close`` so tests can tear down an app while reusing a
        single shared Tk root (destroying and recreating Tk in one process is not
        reliable on macOS and hangs the event loop).
        """
        for job_name in ("_poll_job", "_initial_select_job", "_clock_job"):
            job = getattr(self, job_name, None)
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
                setattr(self, job_name, None)
        self.state.stop_watch(timeout=0.5)

    def on_close(self) -> None:
        self._stop_background()
        self.root.destroy()


def build_app(state: Optional[AppState] = None, root: Optional[tk.Tk] = None) -> VajSaveApp:
    return VajSaveApp(root=root, state=state)
