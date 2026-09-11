import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional, Tuple, Union

from .app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .covers import load_thumbnail, resolve_cover
from .library import Snapshot
from .models import SaveEntry, VolumeInfo
from .ui_theme import (
    PLATFORM_COLORS,
    ROW_COVER,
    ROW_COVER_RADIUS,
    ROW_HEIGHT,
    ROW_PIP_WIDTH,
    SWITCH,
    darken,
    mix,
    save_row,
    status_label,
)

# --- Switch "Basic White" palette (single source of truth: vajsave.ui_theme) -------

BG = SWITCH["bg"]
SIDE = SWITCH["surface"]
CARD = SWITCH["card"]
TEXT = SWITCH["text"]
MUTED = SWITCH["muted"]
LINE = SWITCH["line"]
BLUE = SWITCH["accent"]
ORANGE = SWITCH["warning"]
RED = SWITCH["danger"]

# Selected surfaces are a light-blue tint of the accent so white rows still read.
SELECTED_ROW = mix(BLUE, CARD, 0.84)
SELECTED_LIST = mix(BLUE, CARD, 0.86)

# Sentinel so the cover cache can distinguish a cached ``None`` (negative entry)
# from a cache miss.
_MISSING = object()


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
    ``idle``/``hover``/``pressed`` interaction states, an ``accent`` variant and a
    ``selected`` toggle state. The requested width is measured from the label
    font; the height defaults to the bottom system bar's size with a smaller
    variant for the inspector header.
    """

    BOTTOM_HEIGHT = 32
    ACTION_HEIGHT = 28
    RADIUS = 6
    PADDING = 12
    DOT_SPAN = 18
    MIN_WIDTH = 44

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
            return mix(colors["accent"], colors["card"], 0.84), colors["accent"], ""
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


class SaveList(tk.Canvas):
    """A quiet single-column list of saves drawn on a Canvas.

    One full-width row per visible save: a 3px platform pip, a small cover square
    (or a plain light-grey placeholder), the title (and optional subtitle) and a
    plain text status on the right. It behaves like ``tk.Listbox`` for the bits
    the app relies on (``curselection``/``selection_set``/``selection_clear``):
    click, Ctrl/Cmd-click, Shift-click, Ctrl/Cmd-A, the arrow keys and
    double-click all drive the same selection model.
    """

    ROW_H = ROW_HEIGHT
    PAD = 12
    COVER = ROW_COVER
    COVER_RADIUS = ROW_COVER_RADIUS
    PIP = ROW_PIP_WIDTH
    COVER_CACHE_MAX = 256

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
        self._rows: List[dict] = []
        self._boxes: List[Tuple[float, float, float, float]] = []
        self._selected: set = set()
        self._anchor: Optional[int] = None
        self._active: Optional[int] = None
        self._hover: Optional[int] = None
        self._laying_out = False
        # Reference-keeping thumbnail cache: keyed by (path, mtime, w, h) and
        # holding either a live ``PhotoImage`` or ``None`` (negative cache).
        # Bounded so a long browsing session cannot grow without limit.
        self._cover_cache: "dict[Tuple[str, int, int, int], object]" = {}
        # One strong reference per displayed row, indexed by row, so an LRU
        # eviction of ``_cover_cache`` can never garbage-collect a PhotoImage
        # that is still drawn on the canvas.
        self._cover_refs: "dict[int, object]" = {}

        self.bind("<Enter>", self._on_pointer_enter)
        self.bind("<Leave>", self._on_pointer_leave)
        self.bind("<Motion>", self._on_pointer_motion)
        self.bind("<Button-1>", self._on_button)
        self.bind("<Control-Button-1>", lambda e: self._on_button(e, ctrl=True))
        self.bind("<Command-Button-1>", lambda e: self._on_button(e, ctrl=True))
        self.bind("<Shift-Button-1>", lambda e: self._on_button(e, shift=True))
        self.bind("<Double-Button-1>", self._on_double)
        self.bind("<Up>", lambda e: self.move_active(-1))
        self.bind("<Down>", lambda e: self.move_active(1))
        self.bind("<Return>", lambda e: self._activate_active())
        self.bind("<Control-a>", self._on_select_all)
        self.bind("<Command-a>", self._on_select_all)
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Button-4>", self._on_wheel)
        self.bind("<Button-5>", self._on_wheel)

    # -- model ---------------------------------------------------------------

    def set_rows(self, rows) -> None:
        self._rows = list(rows)
        self._selected = set()
        self._anchor = None
        self._active = None
        self._hover = None
        self._layout()

    def size(self) -> int:
        return len(self._rows)

    @property
    def hover_index(self) -> Optional[int]:
        return self._hover

    def set_hover(self, index: Optional[int]) -> None:
        """Highlight the row under the pointer (``None`` clears it).

        Only the rows whose hover state actually changed are redrawn (tag scoped),
        so a pointer sweep across a long list never triggers a full relayout.
        """
        if index is not None and not (0 <= index < len(self._rows)):
            index = None
        if index == self._hover:
            return
        previous = self._hover
        self._hover = index
        for changed in (previous, index):
            if changed is not None:
                self._redraw_index(changed)

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

    def select_all(self, *, notify: bool = True) -> None:
        if not self._rows:
            return
        self._selected = set(range(len(self._rows)))
        self._anchor = 0
        self._active = len(self._rows) - 1
        self._layout()
        if notify and self._on_select is not None:
            self._on_select(self._active)

    def select_index(
        self,
        index: int,
        *,
        extend: bool = False,
        toggle: bool = False,
        notify: bool = True,
    ) -> None:
        if not (0 <= index < len(self._rows)):
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

    def move_active(self, delta: int) -> str:
        total = len(self._rows)
        if total == 0:
            return "break"
        if self._active is None:
            target = 0
        else:
            target = max(0, min(total - 1, self._active + delta))
        self.select_index(target)
        return "break"

    def activate_index(self, index: int) -> None:
        if not (0 <= index < len(self._rows)):
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

    def _on_select_all(self, _event=None) -> str:
        self.focus_set()
        self.select_all()
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
                width = self.PAD * 2 + self.COVER
        return max(1, width)

    def _viewport_height(self) -> int:
        height = self.winfo_height()
        if height <= 1:
            try:
                height = int(self.cget("height"))
            except (TypeError, ValueError):
                height = self.ROW_H
        return max(1, height)

    def _layout(self) -> None:
        if self._laying_out:
            return
        self._laying_out = True
        try:
            self.delete("all")
            self._boxes = []
            self._cover_refs = {}
            total = len(self._rows)
            width = self._canvas_width()
            if total == 0:
                self.create_text(
                    width / 2,
                    self.ROW_H,
                    text="没有可显示的存档",
                    fill=self.colors["muted"],
                    font=ui_font(13),
                )
                self.configure(scrollregion=(0, 0, width, self.ROW_H))
                return
            x1 = self.PAD
            x2 = max(x1 + 1, width - self.PAD)
            for i, row in enumerate(self._rows):
                y1 = self.PAD + i * self.ROW_H
                y2 = y1 + self.ROW_H
                self._draw_row(i, row, x1, y1, x2, y2)
                self._boxes.append((x1, y1, x2, y2))
            content_h = self.PAD * 2 + total * self.ROW_H
            self.configure(scrollregion=(0, 0, width, content_h))
        finally:
            self._laying_out = False

    def _redraw_index(self, index: int) -> None:
        """Redraw a single row in place, leaving the other rows untouched.

        Used by hover changes so a long list stays responsive; the row's canvas
        items all carry the per-index ``row<N>`` tag.
        """
        if not (0 <= index < len(self._boxes)) or not (0 <= index < len(self._rows)):
            return
        self.delete(f"row{index}")
        x1, y1, x2, y2 = self._boxes[index]
        self._draw_row(index, self._rows[index], x1, y1, x2, y2)

    def _cover_photo(self, path, width: int, height: int):
        """Return a reference-kept ``PhotoImage`` for ``path`` (or ``None``).

        Results (including failures) are cached keyed by path + mtime + size so a
        repaint never re-decodes, and the cache is bounded at
        ``COVER_CACHE_MAX`` entries. Decode errors / vanished files degrade to
        ``None`` and the caller paints the light-grey placeholder instead.
        """
        try:
            stat = Path(path).stat()
            key = (str(path), stat.st_mtime_ns, int(width), int(height))
        except (OSError, TypeError, ValueError):
            return None
        cached = self._cover_cache.pop(key, _MISSING)
        if cached is not _MISSING:
            self._cover_cache[key] = cached  # LRU refresh
            return cached
        photo = None
        image = load_thumbnail(path, width, height, radius=self.COVER_RADIUS)
        if image is not None:
            try:
                from PIL import ImageTk

                photo = ImageTk.PhotoImage(image)
            except Exception:  # noqa: BLE001 - no PhotoImage support means no cover
                photo = None
        self._cover_cache[key] = photo
        while len(self._cover_cache) > self.COVER_CACHE_MAX:
            self._cover_cache.pop(next(iter(self._cover_cache)))
        return photo

    def _draw_row(self, index: int, row: dict, x1: float, y1: float, x2: float, y2: float) -> None:
        colors = self.colors
        selected = index in self._selected
        hovered = index == self._hover

        if selected:
            fill = SELECTED_LIST
        elif hovered:
            fill = colors["hover"]
        else:
            fill = ""
        if fill:
            self.create_polygon(
                _rounded_points(x1, y1 + 2, x2, y2 - 2, 6),
                smooth=True,
                splinesteps=24,
                fill=fill,
                outline="",
                tags=("row-bg", f"bg{index}", f"row{index}"),
            )

        if index < len(self._rows) - 1:
            self.create_line(
                x1,
                y2,
                x2,
                y2,
                fill=colors["line"],
                tags=("row-sep", f"sep{index}", f"row{index}"),
            )

        # The only place a platform colour appears on the page: a 3px pip.
        accent = row.get("accent", colors["accent"])
        self.create_rectangle(
            x1,
            y1 + 8,
            x1 + self.PIP,
            y2 - 8,
            fill=accent,
            outline="",
            tags=("row-pip", f"pip{index}", f"row{index}"),
        )

        cover_size = self.COVER
        cover_x = x1 + 14
        cover_y = y1 + (self.ROW_H - cover_size) // 2
        photo = None
        cover_path = row.get("cover")
        if cover_path:
            photo = self._cover_photo(cover_path, cover_size, cover_size)
        if photo is not None:
            self._cover_refs[index] = photo
            self.create_image(
                cover_x,
                cover_y,
                image=photo,
                anchor="nw",
                tags=("row-cover", f"cover{index}", f"row{index}"),
            )
        else:
            self._cover_refs.pop(index, None)
            self.create_polygon(
                _rounded_points(
                    cover_x,
                    cover_y,
                    cover_x + cover_size,
                    cover_y + cover_size,
                    self.COVER_RADIUS,
                ),
                smooth=True,
                splinesteps=24,
                fill=colors["surface_alt"],
                outline="",
                tags=("row-placeholder", f"ph{index}", f"row{index}"),
            )

        text_x = cover_x + cover_size + 12
        status_text = row.get("status_label", "")
        status_x = x2 - 12
        text_right = status_x - (76 if status_text else 0)
        wrap = max(1, int(text_right - text_x))
        subtitle = row.get("subtitle")
        if subtitle:
            self.create_text(
                text_x,
                y1 + self.ROW_H * 0.34,
                text=row.get("title", ""),
                fill=colors["text"],
                font=ui_font(13),
                width=wrap,
                anchor="w",
                tags=("row-title", f"title{index}", f"row{index}"),
            )
            self.create_text(
                text_x,
                y1 + self.ROW_H * 0.72,
                text=subtitle,
                fill=colors["muted"],
                font=ui_font(11),
                width=wrap,
                anchor="w",
                tags=("row-sub", f"sub{index}", f"row{index}"),
            )
        else:
            self.create_text(
                text_x,
                y1 + self.ROW_H / 2,
                text=row.get("title", ""),
                fill=colors["text"],
                font=ui_font(13),
                width=wrap,
                anchor="w",
                tags=("row-title", f"title{index}", f"row{index}"),
            )
        if status_text:
            self.create_text(
                status_x,
                y1 + self.ROW_H / 2,
                text=status_text,
                fill=colors["muted"],
                font=ui_font(12),
                anchor="e",
                tags=("row-status", f"status{index}", f"row{index}"),
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
        # Buttons/checkbuttons are drawn by ``CanvasButton``; only the inputs still
        # rely on ttk chrome.
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, bordercolor=LINE, padding=6)

    def _create_widgets(self) -> None:
        # Top status bar: app identity on the left, backup stats on the right.
        # No round monogram badge and no clock — just the product name.
        topbar = tk.Frame(self.root, bg=SIDE)
        topbar.pack(fill=tk.X)
        self.topbar = topbar

        brand = tk.Frame(topbar, bg=SIDE)
        brand.pack(side=tk.LEFT, padx=20, pady=12)
        self.brand_frame = brand
        titles = tk.Frame(brand, bg=SIDE)
        titles.pack(side=tk.LEFT)
        tk.Label(titles, text="vaj-save", bg=SIDE, fg=TEXT, font=ui_font(17, "bold")).pack(anchor="w")
        self.subtitle_label = tk.Label(
            titles,
            text="把掌机存档备份下来，按版本管理",
            bg=SIDE,
            fg=MUTED,
            font=ui_font(11),
        )
        self.subtitle_label.pack(anchor="w")

        status_right = tk.Frame(topbar, bg=SIDE)
        status_right.pack(side=tk.RIGHT, padx=20)
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
            selectbackground=SELECTED_LIST,
            selectforeground=TEXT,
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
        list_shell = tk.Frame(mid, bg=BG)
        list_shell.pack(fill=tk.BOTH, expand=True)
        self.save_list = SaveList(
            list_shell,
            on_select=self.on_save_selected,
            on_activate=self.on_row_activated,
        )
        save_scroll = ttk.Scrollbar(list_shell, orient=tk.VERTICAL, command=self.save_list.yview)
        self.save_list.configure(yscrollcommand=save_scroll.set)
        save_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.save_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Right inspector: a compact header (title + three small actions), a
        # definition list, a versions list that fills the remaining height, and a
        # pinned note entry at the bottom.
        right = tk.Frame(body, bg=SIDE, width=360)
        right.pack(side=tk.LEFT, fill=tk.Y)
        right.pack_propagate(False)
        self.detail_panel = right

        header = tk.Frame(right, bg=SIDE)
        header.pack(fill=tk.X, padx=16, pady=(16, 8))
        tk.Label(header, text="详情", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(side=tk.LEFT)
        actions = tk.Frame(header, bg=SIDE)
        actions.pack(side=tk.RIGHT)
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
            button.pack(side=tk.LEFT, padx=(6, 0))
            self._action_buttons.append(button)
            return button

        _action_button("备份", self.on_backup_clicked, accent=True)
        _action_button("恢复", self.on_restore_clicked)
        _action_button("导出 ZIP", self.on_export_zip_clicked)

        detail = tk.Frame(right, bg=SIDE)
        detail.pack(fill=tk.X, padx=16)
        self.detail_name = tk.StringVar(value="未选择游戏")
        tk.Label(
            detail,
            textvariable=self.detail_name,
            bg=SIDE,
            fg=TEXT,
            font=ui_font(15, "bold"),
            wraplength=320,
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        self.detail_vars: Dict[str, tk.StringVar] = {}
        fields = tk.Frame(detail, bg=SIDE)
        fields.pack(fill=tk.X)
        fields.columnconfigure(0, minsize=64)
        fields.columnconfigure(1, weight=1)
        for row_index, (label, key) in enumerate(
            (
                ("机种", "platform"),
                ("状态", "status"),
                ("卡上时间", "source_mtime"),
                ("上次备份", "last_backup"),
                ("路径", "path"),
            )
        ):
            tk.Label(fields, text=label, bg=SIDE, fg=MUTED, font=ui_font(11), anchor="nw").grid(
                row=row_index, column=0, sticky="nw", pady=1
            )
            var = tk.StringVar(value="—")
            self.detail_vars[key] = var
            tk.Label(
                fields,
                textvariable=var,
                bg=SIDE,
                fg=TEXT,
                font=ui_font(12),
                wraplength=270,
                justify="left",
                anchor="w",
            ).grid(row=row_index, column=1, sticky="w", pady=1)
        self.detail_hint_var = tk.StringVar(value="")
        tk.Label(
            detail,
            textvariable=self.detail_hint_var,
            bg=SIDE,
            fg=ORANGE,
            font=ui_font(11),
            wraplength=320,
            justify="left",
            anchor="w",
        ).pack(fill=tk.X, pady=(6, 0))

        note_frame = tk.Frame(right, bg=SIDE)
        note_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=(0, 16))
        tk.Label(note_frame, text="备注", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", pady=(0, 6))
        self.note_var = tk.StringVar()
        note = ttk.Entry(note_frame, textvariable=self.note_var)
        note.pack(fill=tk.X)
        note.bind("<FocusOut>", self.on_note_commit)
        note.bind("<Return>", self.on_note_commit)

        # Versions fill the remaining height between the definition list and the note.
        versions = tk.Frame(right, bg=SIDE)
        versions.pack(fill=tk.BOTH, expand=True, padx=16, pady=(12, 6))
        self.versions_frame = versions
        tk.Label(versions, text="版本", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", pady=(0, 6))
        self.version_list = tk.Listbox(
            versions,
            bg=CARD,
            fg=TEXT,
            selectbackground=SELECTED_LIST,
            selectforeground=TEXT,
            font=ui_font(12),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            height=4,
        )
        self.version_list.pack(fill=tk.BOTH, expand=True)

        # Bottom system bar: quick actions + monitoring toggles.
        bottombar = tk.Frame(self.root, bg=SIDE)
        bottombar.pack(fill=tk.X, padx=24, pady=(0, 8))
        self._bottombar = bottombar
        self._bottom_buttons: List[CanvasButton] = []

        def _bar_button(text: str, command, padx) -> CanvasButton:
            button = CanvasButton(
                bottombar,
                text=text,
                command=command,
                height=CanvasButton.BOTTOM_HEIGHT,
            )
            button.pack(side=tk.LEFT, padx=padx, pady=8)
            self._bottom_buttons.append(button)
            return button

        _bar_button("刷新", self.on_refresh_clicked, (12, 6))
        _bar_button("打开文件夹", self.on_open_folder_clicked, (6, 6))
        _bar_button("打开本地库", self.on_open_library_clicked, (6, 6))
        _bar_button("设置", self.on_settings_clicked, (6, 6))
        self._watch_button = _bar_button("监听插拔", self.on_watch_button_clicked, (12, 0))
        self._watch_button.set_selected(bool(self._watch_var.get()))
        self._hide_unchanged_var.set(bool(self.state.hide_unchanged))
        self._hide_unchanged_button = _bar_button("隐藏已备份", self.on_hide_unchanged_button_clicked, (12, 0))
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

    def on_platform_clicked(self, platform: str) -> None:
        self.state.set_platform_filter(platform)
        self.refresh_platform_ui()
        self.refresh_saves_ui()

    def on_search(self, _event=None) -> None:
        self.state.set_search_query(self.search_var.get())
        self.refresh_saves_ui()

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

    def _clear_detail(self) -> None:
        self.detail_name.set("未选择游戏")
        for var in self.detail_vars.values():
            var.set("—")
        self.detail_hint_var.set("")
        self.note_var.set("")

    def on_save_selected(self, event=None) -> None:
        selection = self.save_list.curselection()
        if not selection or selection[-1] >= len(self._saves_index):
            self._selected_save = None
            self._clear_detail()
            self.refresh_versions_ui()
            return
        # Primary detail follows the active (last) selection in multi-select.
        index = selection[-1]
        save = self._saves_index[index]
        self._selected_save = save
        status = self.state.save_status(save)
        self.detail_name.set(save.display_name or save.path)
        self.detail_vars["platform"].set(PLATFORM_LABELS.get(save.platform, save.platform))
        self.detail_vars["status"].set(status_label(status))
        self.detail_vars["source_mtime"].set(status.source_mtime or "—")
        self.detail_vars["last_backup"].set(status.last_backup_at or "—")
        self.detail_vars["path"].set(save.path)
        self.detail_hint_var.set(
            "卡上时间早于上次备份（可能是回档或拷贝）" if status.mtime_stale else ""
        )
        self.note_var.set(self.state.game_note(save))
        self.refresh_versions_ui()

    def on_row_activated(self, index: int) -> None:
        """Double-clicking a row runs the primary backup action."""
        if 0 <= index < len(self._saves_index):
            self._selected_save = self._saves_index[index]
        self.on_backup_clicked()

    def on_version_selected(self, event=None) -> None:
        selection = self.version_list.curselection()
        if not selection or selection[0] >= len(self._versions_index):
            self._selected_snapshot = None
            return
        self._selected_snapshot = self._versions_index[selection[0]]

    def _selected_saves(self) -> List[SaveEntry]:
        selected: List[SaveEntry] = []
        for index in self.save_list.curselection():
            if 0 <= index < len(self._saves_index):
                selected.append(self._saves_index[index])
        return selected

    def on_backup_clicked(self) -> None:
        """Back up exactly the rows that are currently selected."""
        chosen = self._selected_saves()
        if not chosen:
            self.update_warning("先选择要备份的存档")
            return
        copied = self.state.import_selected_saves(chosen)
        self.update_status(self.state.status_text)
        self.refresh_saves_ui()
        self.refresh_versions_ui()
        self.refresh_stats()
        self.update_warning("" if copied else "备份失败")

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
        rows: List[dict] = []
        restore_index: Optional[int] = None
        for _group_name, saves in self.state.grouped_saves():
            for save in saves:
                status = self.state.save_status(save)
                row = save_row(save, status, starred=self.state.is_starred(save))
                row["cover"] = resolve_cover(save, self.state.library_root)
                rows.append(row)
                self._saves_index.append(save)
                if previous and previous.path == save.path:
                    restore_index = len(self._saves_index) - 1
        self.save_list.set_rows(rows)
        if restore_index is not None:
            self._selected_save = self._saves_index[restore_index]
            self.save_list.select_index(restore_index, notify=False)
            self.on_save_selected()
        else:
            self._clear_detail()
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
        for job_name in ("_poll_job", "_initial_select_job"):
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
