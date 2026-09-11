import subprocess
import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional, Tuple, Union

from .app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .covers import load_thumbnail, resolve_cover
from .identity import (
    STATUS_AMBIGUOUS,
    STATUS_PARTIAL,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
)
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

# --- Archive Desk palette (single source of truth: vajsave.ui_theme) ---------------

BG = SWITCH["bg"]
SIDE = SWITCH["surface"]
CARD = SWITCH["card"]
TEXT = SWITCH["text"]
MUTED = SWITCH["muted"]
LINE = SWITCH["line"]
BLUE = SWITCH["accent"]
ORANGE = SWITCH["warning"]
RED = SWITCH["danger"]
APP_BG = SWITCH["window"]
PANEL_BG = SWITCH["panel"]
PANEL_ALT = SWITCH["panel_alt"]
INK = SWITCH["ink"]
MUTED_STRONG = SWITCH["muted_strong"]
BORDER_SOFT = SWITCH["border_soft"]
STATUS_COLORS = {
    "new": SWITCH["status_blue"],
    "changed": SWITCH["status_orange"],
    "unchanged": SWITCH["status_green"],
}

# Selected surfaces are a light-blue tint of the accent so white rows still read.
SELECTED_ROW = SWITCH["selected"]
SELECTED_LIST = SWITCH["selected"]

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


def _format_ui_timestamp(value: Optional[str]) -> str:
    """把状态层的 ISO 时间压缩成适合列表和详情栏的显示文本。"""
    if not value:
        return "—"
    return str(value).replace("T", " ")[:16]


def _truncate_ui_text(value: object, max_chars: int) -> str:
    """限制单行字段长度，避免表格列之间发生覆盖。"""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"
    return text[: max_chars - 1] + "…"


_IDENTITY_STATUS_LABELS = {
    STATUS_RESOLVED: "已识别",
    STATUS_PARTIAL: "部分识别",
    STATUS_AMBIGUOUS: "多个候选",
    STATUS_UNRESOLVED: "未识别",
}

# Inspector title: ~two lines at 15pt in a 240px wrap. Longer names must not
# grow the right column and shove the status bar off-screen.
_DETAIL_NAME_MAX_CHARS = 28


def save_display(state: AppState, save: SaveEntry) -> Dict[str, str]:
    """List/inspector text for one save, including GameIdentity when resolved."""
    filename = save.display_name or Path(save.path).name
    result = state.resolve_save_identity(save)
    identity = result.identity
    title_id = save.title_id or ""
    header_title = ""
    if identity is not None:
        title_id = identity.title_id or identity.game_code or title_id
        if identity.title and identity.title != filename:
            header_title = identity.title
    subtitle_bits = [bit for bit in (header_title, save.slot, save.user) if bit]
    if result.status in (STATUS_RESOLVED, STATUS_PARTIAL) and identity is not None:
        if identity.game_code and identity.game_code not in subtitle_bits:
            subtitle_bits.append(identity.game_code)
    identity_status = _IDENTITY_STATUS_LABELS.get(result.status, "未识别")
    hint = ""
    if result.status == STATUS_UNRESOLVED and save.platform in ("gba", "nds"):
        hint = "未匹配到 ROM，可在设置中指定 ROM 目录"
    elif result.status == STATUS_AMBIGUOUS:
        hint = "匹配到多个 ROM，未自动选择"
    elif result.reason and result.status == STATUS_PARTIAL:
        hint = result.reason
    return {
        "title": filename,
        "subtitle": " · ".join(subtitle_bits),
        "title_id": title_id or "—",
        "identity_status": identity_status,
        "hint": hint,
    }


def _rounded_points(x1: float, y1: float, x2: float, y2: float, r: float) -> List[float]:
    """Corner points for a smoothed polygon that approximates a rounded rect."""
    r = max(0.0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2,
        x1 + r, y2, x1, y2, x1, y2 - r,
        x1, y1 + r, x1, y1,
    ]


def _draw_platform_mark(canvas: tk.Canvas, platform: str, color: str, background: str) -> None:
    """用小尺寸几何线条绘制平台标记，避免依赖外部图标字体。"""
    canvas.delete("all")
    canvas.configure(bg=background)
    ink = INK
    if platform == "all":
        for offset in (7, 12, 17):
            canvas.create_rectangle(7, offset - 2, 21, offset + 1, fill=color, outline="")
        return
    if platform == "switch":
        canvas.create_rectangle(5, 5, 12, 22, outline=color, width=2)
        canvas.create_rectangle(16, 5, 23, 22, outline=color, width=2)
        canvas.create_oval(8, 8, 10, 10, fill=color, outline="")
        canvas.create_oval(18, 17, 20, 19, fill=color, outline="")
        return
    if platform == "psp":
        canvas.create_oval(5, 8, 12, 15, outline=color, width=2)
        canvas.create_oval(16, 8, 23, 15, outline=color, width=2)
        canvas.create_line(9, 18, 19, 18, fill=color, width=2)
        return
    if platform == "vita":
        canvas.create_rectangle(7, 5, 21, 21, outline=color, width=2)
        canvas.create_line(10, 9, 18, 9, fill=color, width=2)
        canvas.create_line(10, 14, 18, 14, fill=color, width=2)
        return
    if platform in {"3ds", "nds"}:
        canvas.create_rectangle(6, 5, 22, 11, outline=color, width=2)
        canvas.create_rectangle(6, 14, 22, 21, outline=color, width=2)
        canvas.create_line(10, 11, 10, 14, fill=color, width=2)
        canvas.create_line(18, 11, 18, 14, fill=color, width=2)
        return
    if platform == "gba":
        canvas.create_rectangle(5, 7, 23, 19, outline=color, width=2)
        canvas.create_line(9, 13, 14, 13, fill=color, width=2)
        canvas.create_oval(17, 11, 20, 14, fill=color, outline="")
        return
    canvas.create_rectangle(6, 6, 22, 20, outline=ink, width=2)


class CanvasButton(tk.Canvas):
    """A flat rounded button drawn on a Canvas.

    Replaces every ``ttk`` button/checkbutton in the app so the Basic White
    surfaces never fall back to the platform's native chrome. Exposes the
    ``idle``/``hover``/``pressed`` interaction states, an ``accent`` variant and a
    ``selected`` toggle state. The requested width is measured from the label
    font; the height defaults to the bottom system bar's size with a smaller
    variant for the inspector header.
    """

    BOTTOM_HEIGHT = 34
    ACTION_HEIGHT = 32
    RADIUS = 6
    PADDING = 14
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
            return colors["line_soft"], colors["ink"], colors["line_strong"]
        if interaction == "hover":
            return colors["panel_alt"], colors["ink"], colors["border_soft"]
        if self.selected:
            return colors["selected_soft"], colors["accent"], colors["accent"]
        return colors["card"], colors["ink"], colors["border_soft"]

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

    One full-width row per visible save: a small cover square (or a plain
    light-grey placeholder), the title block, optional metadata columns and a
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

    def _fit_text(self, value: object, font_spec, max_px: int) -> str:
        """Single-line ellipsis using pixel width so CJK names cannot wrap."""
        text = str(value or "")
        if max_px <= 8:
            return "…" if text else ""
        font = tkfont.Font(font=font_spec, root=self)
        if font.measure(text) <= max_px:
            return text
        ellipsis = "…"
        lo, hi = 0, len(text)
        best = ellipsis
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = text[:mid] + ellipsis
            if font.measure(candidate) <= max_px:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        return best

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

        # Keep the row edge neutral. Platform identity is shown once in the left
        # rail; repeating saturated strips on every row made the table noisy.
        self.create_rectangle(
            x1,
            y1 + 8,
            x1 + self.PIP,
            y2 - 8,
            fill=colors["line_soft"],
            outline="",
            tags=("row-edge", f"edge{index}", f"row{index}"),
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
        content_width = x2 - x1
        show_platform = content_width >= 430
        show_date = content_width >= 500
        show_title_id = content_width >= 620
        date_text = row.get("last_backup", "") if show_date else ""
        platform_text = row.get("platform_label", "") if show_platform else ""
        title_id_text = row.get("title_id", "") if show_title_id else ""
        status_right = x2 - 12
        status_left = status_right - 95
        cursor = status_left
        date_x = title_id_x = platform_x = None
        if date_text:
            date_x = cursor - 10
            cursor = date_x - 115
        if title_id_text:
            title_id_x = cursor - 10
            cursor = title_id_x - 110
        if platform_text:
            platform_x = cursor - 10
            cursor = platform_x - 75
        text_right = cursor - 16 if (platform_text or title_id_text or date_text) else status_left - 16
        wrap = max(1, int(text_right - text_x))
        subtitle = row.get("subtitle")
        title_font = ui_font(13)
        sub_font = ui_font(11)
        title_text = self._fit_text(row.get("title", ""), title_font, wrap)
        subtitle_text = self._fit_text(subtitle, sub_font, wrap)
        if subtitle:
            self.create_text(
                text_x,
                y1 + self.ROW_H * 0.34,
                text=title_text,
                fill=colors["ink"],
                font=title_font,
                anchor="w",
                tags=("row-title", f"title{index}", f"row{index}"),
            )
            self.create_text(
                text_x,
                y1 + self.ROW_H * 0.72,
                text=subtitle_text,
                fill=colors["muted"],
                font=sub_font,
                anchor="w",
                tags=("row-sub", f"sub{index}", f"row{index}"),
            )
        else:
            self.create_text(
                text_x,
                y1 + self.ROW_H / 2,
                text=title_text,
                fill=colors["ink"],
                font=title_font,
                anchor="w",
                tags=("row-title", f"title{index}", f"row{index}"),
            )
        if status_text:
            status_color = STATUS_COLORS.get(row.get("status"), colors["muted_strong"])
            if content_width >= 430:
                dot_x = status_left
                self.create_oval(
                    dot_x,
                    y1 + self.ROW_H * 0.30,
                    dot_x + 8,
                    y1 + self.ROW_H * 0.30 + 8,
                    fill=status_color,
                    outline="",
                    tags=("row-status-dot", f"status-dot{index}", f"row{index}"),
                )
                self.create_text(
                    dot_x + 14,
                    y1 + self.ROW_H * 0.34,
                    text=status_text,
                    fill=colors["ink"],
                    font=ui_font(11, "bold"),
                    anchor="w",
                    tags=("row-status", f"status{index}", f"row{index}"),
                )
                version_count = row.get("version_count")
                if version_count is not None:
                    self.create_text(
                        dot_x + 14,
                        y1 + self.ROW_H * 0.68,
                        text=f"{version_count} 个版本",
                        fill=colors["muted_strong"],
                        font=ui_font(10),
                        anchor="w",
                        tags=("row-version-count", f"version-count{index}", f"row{index}"),
                    )
            else:
                self.create_oval(
                    status_right - 44,
                    y1 + self.ROW_H / 2 - 4,
                    status_right - 36,
                    y1 + self.ROW_H / 2 + 4,
                    fill=status_color,
                    outline="",
                    tags=("row-status-dot", f"status-dot{index}", f"row{index}"),
                )
                self.create_text(
                    status_right - 28,
                    y1 + self.ROW_H / 2,
                    text=status_text,
                    fill=colors["muted_strong"],
                    font=ui_font(12),
                    anchor="e",
                    tags=("row-status", f"status{index}", f"row{index}"),
                )
        if platform_text and platform_x is not None:
            self.create_text(
                platform_x,
                y1 + self.ROW_H / 2,
                text=_truncate_ui_text(platform_text, 10),
                fill=colors["muted_strong"],
                font=ui_font(11),
                anchor="e",
                tags=("row-platform", f"platform{index}", f"row{index}"),
            )
        if title_id_text and title_id_x is not None:
            self.create_text(
                title_id_x,
                y1 + self.ROW_H / 2,
                text=_truncate_ui_text(title_id_text, 15),
                fill=colors["muted_strong"],
                font=ui_font(10),
                anchor="e",
                tags=("row-title-id", f"title-id{index}", f"row{index}"),
            )
        if date_text and date_x is not None:
            self.create_text(
                date_x,
                y1 + self.ROW_H / 2,
                text=_truncate_ui_text(date_text, 16),
                fill=colors["muted_strong"],
                font=ui_font(11),
                anchor="e",
                tags=("row-date", f"date{index}", f"row{index}"),
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
        # The archive desk needs enough room for its three semantic columns. A
        # smaller window is still usable because the center table hides optional
        # columns, but the columns themselves must never collapse into each other.
        self.root.minsize(1320, 780)
        self.root.geometry("1480x900")
        self.root.configure(bg=APP_BG)

    def _apply_theme(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=APP_BG)
        style.configure("TLabel", background=APP_BG, foreground=INK, font=ui_font(13))
        # Buttons/checkbuttons are drawn by ``CanvasButton``; only the inputs still
        # rely on ttk chrome.
        style.configure(
            "TEntry",
            fieldbackground=CARD,
            foreground=INK,
            insertcolor=INK,
            bordercolor=BORDER_SOFT,
            lightcolor=BORDER_SOFT,
            darkcolor=BORDER_SOFT,
            padding=(8, 6),
        )
        style.map(
            "TEntry",
            fieldbackground=[("focus", CARD)],
            bordercolor=[("focus", BLUE)],
            lightcolor=[("focus", BLUE)],
            darkcolor=[("focus", BLUE)],
        )

    def _create_widgets(self) -> None:
        """Build the Archive Desk shell.

        The main body deliberately uses ``grid`` instead of nested ``pack``
        expansion. The left rail and inspector have stable widths, while only
        the archive table absorbs extra space. This is the layout boundary that
        prevents detail text and table columns from colliding.
        """
        topbar = tk.Frame(self.root, bg=APP_BG, height=68)
        topbar.pack(fill=tk.X)
        topbar.pack_propagate(False)
        self.topbar = topbar

        brand = tk.Frame(topbar, bg=APP_BG)
        brand.pack(side=tk.LEFT, padx=24, pady=12)
        self.brand_frame = brand
        brand_mark = tk.Canvas(brand, width=42, height=42, bg=APP_BG, highlightthickness=0, bd=0)
        brand_mark.pack(side=tk.LEFT, padx=(0, 12))
        brand_mark.create_rectangle(2, 2, 40, 40, fill=INK, outline="")
        brand_mark.create_rectangle(10, 12, 32, 16, fill=APP_BG, outline="")
        brand_mark.create_rectangle(10, 19, 32, 23, fill=APP_BG, outline="")
        brand_mark.create_rectangle(10, 26, 32, 30, fill=APP_BG, outline="")
        titles = tk.Frame(brand, bg=APP_BG)
        titles.pack(side=tk.LEFT)
        tk.Label(titles, text="vaj-save", bg=APP_BG, fg=INK, font=ui_font(19, "bold")).pack(anchor="w")
        self.subtitle_label = tk.Label(
            titles,
            text="守护每一段游戏时光",
            bg=APP_BG,
            fg=MUTED_STRONG,
            font=ui_font(11),
        )
        self.subtitle_label.pack(anchor="w", pady=(1, 0))

        top_tools = tk.Frame(topbar, bg=APP_BG)
        top_tools.pack(side=tk.RIGHT, padx=24)
        self.stats_var = tk.StringVar(value="")
        tk.Label(top_tools, textvariable=self.stats_var, bg=APP_BG, fg=MUTED_STRONG, font=ui_font(11)).pack(
            side=tk.RIGHT, padx=(18, 0)
        )
        CanvasButton(top_tools, text="帮助", command=lambda: self.update_status("帮助中心暂未配置"), height=30, padding=10).pack(
            side=tk.RIGHT, padx=(6, 0)
        )
        CanvasButton(top_tools, text="设置", command=self.on_settings_clicked, height=30, padding=10).pack(side=tk.RIGHT)

        body = tk.Frame(self.root, bg=APP_BG)
        body.pack(fill=tk.BOTH, expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, minsize=236, weight=0)
        body.grid_columnconfigure(1, minsize=640, weight=1)
        body.grid_columnconfigure(2, minsize=430, weight=0)

        left_shell = tk.Frame(body, bg=BORDER_SOFT, width=236, highlightthickness=0)
        left_shell.grid(row=0, column=0, sticky="nsew")
        left_shell.grid_propagate(False)
        left = tk.Frame(left_shell, bg=PANEL_BG)
        left.pack(fill=tk.BOTH, expand=True, padx=(0, 1))
        left.grid_rowconfigure(1, weight=0)
        left.grid_rowconfigure(3, weight=0)
        left.grid_rowconfigure(5, weight=1)
        left.grid_columnconfigure(0, weight=1)
        tk.Label(left, text="平台", bg=PANEL_BG, fg=INK, font=ui_font(12, "bold")).grid(
            row=0, column=0, sticky="w", padx=18, pady=(20, 8)
        )
        self.platform_box = tk.Frame(left, bg=PANEL_BG)
        self.platform_box.grid(row=1, column=0, sticky="ew", padx=10)

        device_head = tk.Frame(left, bg=PANEL_BG)
        device_head.grid(row=2, column=0, sticky="ew", padx=18, pady=(18, 8))
        tk.Label(device_head, text="已连接设备", bg=PANEL_BG, fg=INK, font=ui_font(12, "bold")).pack(side=tk.LEFT)
        CanvasButton(device_head, text="刷新", command=self.on_refresh_clicked, height=28, padding=8).pack(side=tk.RIGHT)
        volume_shell = tk.Frame(left, bg=CARD, highlightbackground=BORDER_SOFT, highlightthickness=1, height=96)
        volume_shell.grid(row=3, column=0, sticky="ew", padx=12)
        volume_shell.grid_propagate(False)
        self.vol_list = tk.Listbox(
            volume_shell,
            bg=CARD,
            fg=INK,
            selectbackground=SELECTED_LIST,
            selectforeground=INK,
            font=ui_font(10),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            selectborderwidth=0,
            height=4,
        )
        self.vol_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        tk.Label(left, text="＋ 添加设备", bg=PANEL_BG, fg=BLUE, font=ui_font(11, "bold")).grid(
            row=4, column=0, sticky="w", padx=18, pady=(8, 12)
        )

        library_frame = tk.Frame(left, bg=PANEL_ALT, highlightbackground=BORDER_SOFT, highlightthickness=1)
        library_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=16)
        library_head = tk.Frame(library_frame, bg=PANEL_ALT)
        library_head.pack(fill=tk.X, padx=12, pady=(12, 4))
        tk.Label(library_head, text="本地存档库", bg=PANEL_ALT, fg=INK, font=ui_font(11, "bold")).pack(side=tk.LEFT)
        CanvasButton(library_head, text="打开", command=self.on_open_library_clicked, height=28, padding=8).pack(side=tk.RIGHT)
        self.library_path_var = tk.StringVar(value=str(self.state.library_root))
        tk.Label(
            library_frame,
            textvariable=self.library_path_var,
            bg=PANEL_ALT,
            fg=MUTED_STRONG,
            font=ui_font(10),
            anchor="w",
            justify="left",
            wraplength=190,
        ).pack(fill=tk.X, padx=12, pady=(0, 12))

        mid_shell = tk.Frame(body, bg=BORDER_SOFT, highlightthickness=0)
        mid_shell.grid(row=0, column=1, sticky="nsew")
        mid = tk.Frame(mid_shell, bg=APP_BG)
        mid.pack(fill=tk.BOTH, expand=True, padx=(1, 0))
        mid.grid_rowconfigure(2, weight=1)
        mid.grid_columnconfigure(0, weight=1)
        middle_header = tk.Frame(mid, bg=APP_BG)
        middle_header.grid(row=0, column=0, sticky="ew", padx=22, pady=(22, 12))
        middle_header.grid_columnconfigure(0, weight=1)
        title_group = tk.Frame(middle_header, bg=APP_BG)
        title_group.grid(row=0, column=0, sticky="w")
        tk.Label(title_group, text="存档档案", bg=APP_BG, fg=INK, font=ui_font(20, "bold")).pack(anchor="w")
        self.archive_hint_var = tk.StringVar(value="按最近备份时间排序")
        tk.Label(title_group, textvariable=self.archive_hint_var, bg=APP_BG, fg=MUTED_STRONG, font=ui_font(10)).pack(anchor="w", pady=(3, 0))
        controls = tk.Frame(middle_header, bg=APP_BG)
        controls.grid(row=0, column=1, sticky="e")
        search_shell = tk.Frame(controls, bg=CARD, width=222, height=34, highlightbackground=BORDER_SOFT, highlightthickness=1)
        search_shell.pack(side=tk.LEFT, padx=(0, 8))
        search_shell.pack_propagate(False)
        tk.Label(search_shell, text="⌕", bg=CARD, fg=MUTED_STRONG, font=ui_font(15)).pack(side=tk.LEFT, padx=(10, 4))
        self.search_var = tk.StringVar()
        search = ttk.Entry(search_shell, textvariable=self.search_var)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=2)
        search.bind("<KeyRelease>", self.on_search)
        sort_box = tk.Frame(controls, bg=CARD, width=142, height=34, highlightbackground=BORDER_SOFT, highlightthickness=1)
        sort_box.pack(side=tk.LEFT)
        sort_box.pack_propagate(False)
        tk.Label(sort_box, text="↕  最近备份时间", bg=CARD, fg=INK, font=ui_font(10, "bold")).pack(expand=True)

        column_header = tk.Frame(mid, bg=APP_BG)
        column_header.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 7))
        column_header.columnconfigure(0, weight=1)
        for column, text, width in ((0, "游戏", 0), (1, "平台", 76), (2, "Title ID", 112), (3, "最近备份", 126), (4, "状态", 86)):
            column_header.columnconfigure(column, minsize=width)
            tk.Label(column_header, text=text, bg=APP_BG, fg=MUTED_STRONG, font=ui_font(10, "bold"), anchor="w").grid(
                row=0, column=column, sticky="ew", padx=(0, 12)
            )
        list_shell = tk.Frame(mid, bg=APP_BG, highlightbackground=BORDER_SOFT, highlightthickness=1)
        list_shell.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 18))
        list_colors = dict(SWITCH)
        list_colors.update(bg=APP_BG, surface=APP_BG, surface_alt=PANEL_ALT, panel_alt=PANEL_ALT, text=INK, ink=INK, muted_strong=MUTED_STRONG, line=BORDER_SOFT)
        self.save_list = SaveList(list_shell, on_select=self.on_save_selected, on_activate=self.on_row_activated, colors=list_colors)
        self.save_list.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        right_shell = tk.Frame(body, bg=BORDER_SOFT, width=430, highlightthickness=0)
        right_shell.grid(row=0, column=2, sticky="nsew")
        right_shell.grid_propagate(False)
        self.detail_panel = right_shell
        right = tk.Frame(right_shell, bg=PANEL_BG)
        right.pack(fill=tk.BOTH, expand=True, padx=(1, 0))
        right.grid_rowconfigure(4, weight=1)
        right.grid_columnconfigure(0, weight=1)

        header = tk.Frame(right, bg=PANEL_BG)
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 8))
        tk.Label(header, text="存档详情", bg=PANEL_BG, fg=INK, font=ui_font(12, "bold")).pack(side=tk.LEFT)
        self.actions_frame = header
        self._action_buttons: List[CanvasButton] = []

        detail = tk.Frame(right, bg=PANEL_BG)
        detail.grid(row=1, column=0, sticky="ew", padx=20)
        detail.grid_columnconfigure(1, weight=1)
        preview = tk.Frame(detail, bg=PANEL_BG)
        preview.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        self.detail_cover_canvas = tk.Canvas(preview, width=112, height=148, bg=PANEL_BG, highlightthickness=0, bd=0)
        self.detail_cover_canvas.pack(side=tk.LEFT, padx=(0, 14))
        self.detail_cover_ref = None
        self.detail_name = tk.StringVar(value="未选择游戏")
        name_block = tk.Frame(preview, bg=PANEL_BG)
        name_block.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tk.Label(
            name_block,
            textvariable=self.detail_name,
            bg=PANEL_BG,
            fg=INK,
            font=ui_font(15, "bold"),
            wraplength=240,
            justify="left",
            anchor="nw",
            height=2,
        ).pack(fill=tk.X, pady=(4, 5))
        self.detail_subtitle_var = tk.StringVar(value="")
        tk.Label(
            name_block,
            textvariable=self.detail_subtitle_var,
            bg=PANEL_BG,
            fg=MUTED_STRONG,
            font=ui_font(10),
            anchor="w",
            wraplength=240,
            justify="left",
            height=1,
        ).pack(fill=tk.X)

        self.detail_vars: Dict[str, tk.StringVar] = {}
        fields = tk.Frame(detail, bg=PANEL_BG)
        fields.grid(row=1, column=0, columnspan=2, sticky="ew")
        for column in (1, 3):
            fields.columnconfigure(column, weight=1)
        pairs = (
            (("平台", "platform"), ("识别", "identity")),
            (("Title ID", "title_id"), ("版本", "version")),
            (("最近备份", "last_backup"), ("状态", "status")),
        )
        for row_index, pair in enumerate(pairs):
            for pair_index, (label, key) in enumerate(pair):
                label_column = pair_index * 2
                value_column = label_column + 1
                tk.Label(fields, text=label, bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10), anchor="nw").grid(
                    row=row_index, column=label_column, sticky="nw", pady=2, padx=(0, 7)
                )
                var = tk.StringVar(value="—")
                self.detail_vars[key] = var
                tk.Label(fields, textvariable=var, bg=PANEL_BG, fg=INK, font=ui_font(10), anchor="w").grid(
                    row=row_index, column=value_column, sticky="ew", pady=2, padx=(0, 10 if pair_index == 0 else 0)
                )
        tk.Label(fields, text="存档位置", bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10), anchor="nw").grid(
            row=3, column=0, sticky="nw", pady=(5, 2), padx=(0, 7)
        )
        self.detail_vars["path"] = tk.StringVar(value="—")
        tk.Label(
            fields,
            textvariable=self.detail_vars["path"],
            bg=PANEL_BG,
            fg=INK,
            font=ui_font(10),
            wraplength=300,
            justify="left",
            anchor="nw",
            height=2,
        ).grid(row=3, column=1, columnspan=3, sticky="ew", pady=(5, 2))
        for key in ("status", "source_mtime"):
            self.detail_vars.setdefault(key, tk.StringVar(value="—"))
        self.detail_hint_var = tk.StringVar(value="")
        tk.Label(detail, textvariable=self.detail_hint_var, bg=PANEL_BG, fg=ORANGE, font=ui_font(10), wraplength=380, justify="left", anchor="w").grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(5, 0)
        )

        action_area = tk.Frame(right, bg=PANEL_BG)
        action_area.grid(row=2, column=0, sticky="ew", padx=20, pady=(14, 10))
        action_area.grid_columnconfigure(0, weight=1)
        primary = CanvasButton(action_area, text="备份存档", command=self.on_backup_clicked, variant="accent", height=40, padding=12)
        primary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._action_buttons.append(primary)
        secondary = tk.Frame(action_area, bg=PANEL_BG)
        secondary.grid(row=1, column=0, sticky="ew")
        for column in range(3):
            secondary.grid_columnconfigure(column, weight=1)
        restore = CanvasButton(secondary, text="恢复", command=self.on_restore_clicked, height=34, padding=12)
        restore.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        export = CanvasButton(secondary, text="导出 ZIP", command=self.on_export_zip_clicked, height=34, padding=12)
        export.grid(row=0, column=1, sticky="ew", padx=5)
        location = CanvasButton(secondary, text="打开位置", command=self.on_open_save_location_clicked, height=34, padding=12)
        location.grid(row=0, column=2, sticky="ew", padx=(5, 0))
        self._action_buttons.extend((restore, export))

        versions = tk.Frame(right, bg=PANEL_BG)
        versions.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 10))
        versions.grid_rowconfigure(1, weight=1)
        versions.grid_columnconfigure(0, weight=1)
        self.versions_frame = versions
        version_head = tk.Frame(versions, bg=PANEL_BG)
        version_head.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.version_title_var = tk.StringVar(value="版本历史")
        tk.Label(version_head, textvariable=self.version_title_var, bg=PANEL_BG, fg=INK, font=ui_font(13, "bold")).pack(side=tk.LEFT)
        tk.Label(version_head, text="版本    备份时间", bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10)).pack(side=tk.RIGHT)
        self.version_list = tk.Listbox(
            versions,
            bg=CARD,
            fg=INK,
            selectbackground=SELECTED_LIST,
            selectforeground=INK,
            font=("Menlo", 10) if sys.platform == "darwin" else ui_font(10),
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            bd=0,
            activestyle="none",
            selectborderwidth=0,
            height=4,
            exportselection=False,
        )
        self.version_list.grid(row=1, column=0, sticky="nsew")

        note_frame = tk.Frame(right, bg=PANEL_BG)
        note_frame.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 18))
        note_header = tk.Frame(note_frame, bg=PANEL_BG)
        note_header.pack(fill=tk.X, pady=(0, 6))
        tk.Label(note_header, text="备注", bg=PANEL_BG, fg=INK, font=ui_font(12, "bold")).pack(side=tk.LEFT)
        tk.Label(note_header, text="自动保存", bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10)).pack(side=tk.RIGHT)
        self.note_var = tk.StringVar()
        note = ttk.Entry(note_frame, textvariable=self.note_var)
        note.pack(fill=tk.X)
        note.bind("<FocusOut>", self.on_note_commit)
        note.bind("<Return>", self.on_note_commit)

        bottombar = tk.Frame(self.root, bg=APP_BG, height=42, highlightbackground=BORDER_SOFT, highlightthickness=1)
        bottombar.pack(fill=tk.X)
        bottombar.pack_propagate(False)
        self._bottombar = bottombar
        self._bottom_buttons: List[CanvasButton] = []
        self.statusrow = tk.Frame(bottombar, bg=APP_BG)
        self.statusrow.pack(fill=tk.BOTH, expand=True, padx=20)
        self.status_label_var = tk.StringVar(value="准备好了，插上掌机或打开文件夹就可以开始")
        self.status_label = tk.Label(self.statusrow, textvariable=self.status_label_var, bg=APP_BG, fg=MUTED_STRONG, anchor="w", font=ui_font(10))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.warning_label_var = tk.StringVar(value="")
        self.warning_label = tk.Label(self.statusrow, textvariable=self.warning_label_var, bg=APP_BG, fg=RED, anchor="e", font=ui_font(10))
        self.warning_label.pack(side=tk.RIGHT, padx=(8, 12))
        self._watch_button = CanvasButton(self.statusrow, text="监听中", command=self.on_watch_button_clicked, height=30, padding=9, selected=True)
        self._watch_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._watch_button.set_selected(bool(self._watch_var.get()))
        self._bottom_buttons.append(self._watch_button)
        self._hide_unchanged_var.set(bool(self.state.hide_unchanged))
        self._hide_unchanged_button = CanvasButton(self.statusrow, text="隐藏已备份", command=self.on_hide_unchanged_button_clicked, height=30, padding=9)
        self._hide_unchanged_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._hide_unchanged_button.set_selected(bool(self._hide_unchanged_var.get()))
        self._bottom_buttons.append(self._hide_unchanged_button)

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
            row = tk.Frame(self.platform_box, bg=PANEL_BG, cursor="hand2", height=38)
            row.pack(fill=tk.X, pady=2)
            row.pack_propagate(False)
            pip = tk.Frame(row, bg=PANEL_BG, width=3)
            pip.pack(side=tk.LEFT, fill=tk.Y)
            icon = tk.Canvas(row, width=28, height=28, bg=PANEL_BG, highlightthickness=0, bd=0)
            icon.pack(side=tk.LEFT, padx=(8, 5))
            _draw_platform_mark(icon, key, PLATFORM_COLORS.get(key, BLUE), PANEL_BG)
            name = tk.Label(row, text=PLATFORM_LABELS.get(key, key), bg=PANEL_BG, fg=INK, font=ui_font(11), anchor="w")
            name.pack(side=tk.LEFT, fill=tk.X, expand=True)
            badge = tk.Label(row, text="0", bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10))
            badge.pack(side=tk.RIGHT, padx=12)
            for widget in (row, pip, icon, name, badge):
                widget.bind("<Button-1>", lambda _e, platform=key: self.on_platform_clicked(platform))
            self._platform_rows[key] = {"row": row, "icon": icon, "name": name, "badge": badge, "pip": pip}
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
            row_bg = SWITCH["selected_soft"] if selected else PANEL_BG
            widgets["row"].configure(bg=row_bg)
            widgets["icon"].configure(bg=row_bg)
            widgets["name"].configure(bg=row_bg)
            widgets["badge"].configure(bg=row_bg, text=str(count))
            widgets["pip"].configure(bg=row_bg)
            _draw_platform_mark(widgets["icon"], key, PLATFORM_COLORS.get(key, BLUE), row_bg)

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
        self.detail_subtitle_var.set("")
        for var in self.detail_vars.values():
            var.set("—")
        self.detail_hint_var.set("")
        self.note_var.set("")
        self._render_detail_cover(None)

    def _render_detail_cover(self, save: Optional[SaveEntry]) -> None:
        """绘制当前存档封面，且不让详情面板依赖列表缓存。"""
        canvas = getattr(self, "detail_cover_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        self.detail_cover_ref = None
        cover_path = resolve_cover(save, self.state.library_root) if save else None
        if cover_path:
            image = load_thumbnail(cover_path, 108, 144, radius=8)
            if image is not None:
                try:
                    from PIL import ImageTk

                    self.detail_cover_ref = ImageTk.PhotoImage(image)
                    canvas.create_image(2, 2, image=self.detail_cover_ref, anchor="nw")
                    return
                except Exception:  # noqa: BLE001 - the placeholder is a valid fallback
                    self.detail_cover_ref = None
        canvas.create_polygon(
            _rounded_points(2, 2, 110, 146, 8),
            smooth=True,
            splinesteps=24,
            fill=SWITCH["surface_alt"],
            outline="",
        )

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
        view = save_display(self.state, save)
        self.detail_name.set(_truncate_ui_text(view["title"], _DETAIL_NAME_MAX_CHARS))
        self.detail_subtitle_var.set(_truncate_ui_text(view["subtitle"], 40))
        self._render_detail_cover(save)
        self.detail_vars["platform"].set(PLATFORM_LABELS.get(save.platform, save.platform))
        self.detail_vars["identity"].set(view["identity_status"])
        self.detail_vars["title_id"].set(view["title_id"])
        snapshots = self.state.versions_for_entry(save)
        self.detail_vars["version"].set(f"{len(snapshots)} 个版本" if snapshots else "尚未备份")
        self.detail_vars["status"].set(status_label(status))
        self.detail_vars["source_mtime"].set(_format_ui_timestamp(status.source_mtime))
        self.detail_vars["last_backup"].set(_format_ui_timestamp(status.last_backup_at))
        self.detail_vars["path"].set(save.path)
        stale = "卡上时间早于上次备份（可能是回档或拷贝）" if status.mtime_stale else ""
        self.detail_hint_var.set(stale or view["hint"])
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

    def on_open_save_location_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("先选择一个存档")
            return
        ok, msg = open_in_file_manager(self._selected_save.path)
        self.update_status(msg if ok else "定位存档失败")
        self.update_warning("" if ok else msg)

    def _apply_library_root(self, path: str) -> None:
        """Apply a new library root chosen in the settings dialog."""
        self.state.set_library_root(path)
        self.refresh_saves_ui()
        self.refresh_stats()
        self.update_status(f"备份库已切换到 {self.state.library_root}")
        self.update_warning("")

    def _apply_rom_dirs(self, gba_rom_dir, nds_rom_dir) -> None:
        """Apply the optional cartridge ROM directories chosen in the settings dialog."""
        self.state.set_rom_dirs(gba_rom_dir, nds_rom_dir)
        self.refresh_saves_ui()
        self.update_status("ROM 目录已更新")

    def on_settings_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("设置")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        def add_dir_row(label: str, value: str, browse_title: str) -> tk.StringVar:
            """A labelled directory entry with its own 浏览… button."""
            tk.Label(dialog, text=label, bg=BG, fg=TEXT, font=ui_font(13, "bold")).pack(anchor="w", padx=16, pady=(12, 6))
            row = tk.Frame(dialog, bg=BG)
            row.pack(fill=tk.X, padx=16)
            var = tk.StringVar(value=value)
            ttk.Entry(row, textvariable=var, width=42).pack(side=tk.LEFT, fill=tk.X, expand=True)

            def browse() -> None:
                chosen = filedialog.askdirectory(
                    title=browse_title,
                    parent=dialog,
                    initialdir=var.get() or None,
                )
                if chosen:
                    var.set(chosen)

            CanvasButton(row, text="浏览…", command=browse).pack(side=tk.LEFT, padx=(8, 0))
            return var

        path_var = add_dir_row("本地备份库路径", str(self.state.library_root), "选择备份库目录")
        gba_var = add_dir_row("GBA ROM 目录（可选）", str(self.state.gba_rom_dir or ""), "选择 GBA ROM 目录")
        nds_var = add_dir_row("NDS ROM 目录（可选）", str(self.state.nds_rom_dir or ""), "选择 NDS ROM 目录")

        def save() -> None:
            chosen = path_var.get().strip()
            if not chosen:
                return
            gba = gba_var.get().strip()
            nds = nds_var.get().strip()
            dialog.destroy()
            self._apply_library_root(chosen)
            self._apply_rom_dirs(gba or None, nds or None)

        def cancel() -> None:
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg=BG)
        btn_row.pack(fill=tk.X, padx=16, pady=16)
        CanvasButton(btn_row, text="取消", command=cancel).pack(side=tk.RIGHT)
        CanvasButton(btn_row, text="保存", variant="accent", command=save).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.grab_set()

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
            mount_name = Path(vol.mount_point).name or str(vol.mount_point)
            self.vol_list.insert(tk.END, f"●  {vol.name}   {mount_name}")
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
                view = save_display(self.state, save)
                row["title"] = view["title"]
                row["subtitle"] = view["subtitle"]
                row["cover"] = resolve_cover(save, self.state.library_root)
                row["platform_label"] = PLATFORM_LABELS.get(save.platform, save.platform)
                row["title_id"] = view["title_id"]
                row["last_backup"] = _format_ui_timestamp(status.last_backup_at)
                row["version_count"] = len(self.state.versions_for_entry(save))
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
        self.version_title_var.set("版本历史")
        if not self._selected_save:
            return
        snapshots = list(reversed(self.state.versions_for_entry(self._selected_save)))
        self.version_title_var.set(f"版本历史  ·  {len(snapshots)} 个版本")
        for snap in snapshots:
            created = _format_ui_timestamp(snap.created_at)
            self.version_list.insert(tk.END, f"○  {snap.id}    {created}")
            self._versions_index.append(snap)
        if snapshots:
            self.version_list.selection_set(0)
            self._selected_snapshot = self._versions_index[0]

    def refresh_stats(self) -> None:
        stats = self.state.collection_stats()
        self.stats_var.set(f"已备份 {stats['games']} 款游戏 · {stats['versions']} 个版本")
        if hasattr(self, "archive_hint_var"):
            self.archive_hint_var.set(f"共 {len(self.state.visible_saves())} 个可见存档 · 按最近备份时间排序")
        if hasattr(self, "library_path_var"):
            self.library_path_var.set(str(self.state.library_root))

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
