"""Standalone canvas-drawn widgets for the Archive Desk.

``CanvasButton`` and ``SaveList`` are self-contained: they depend only on the
shared design tokens in :mod:`vajsave.ui_theme` and the cover helper in
:mod:`vajsave.covers`, never on :class:`~vajsave.app_ui.VajSaveApp`.  Keeping
them here trims ``app_ui`` down to the app shell and its event wiring.
"""

import sys
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .covers import load_thumbnail
from .ui_theme import (
    ROW_COVER,
    ROW_COVER_RADIUS,
    ROW_HEIGHT,
    ROW_PIP_WIDTH,
    SWITCH,
    darken,
    mix,
)

# Selected surfaces are a light-blue tint of the accent so white rows still read.
SELECTED_LIST = SWITCH["selected"]

# Row status colours; the status text itself is produced by ``ui_theme.save_row``.
STATUS_COLORS = {
    "new": SWITCH["status_blue"],
    "changed": SWITCH["status_orange"],
    "unchanged": SWITCH["status_green"],
}

# Sentinel so the cover cache can distinguish a cached ``None`` (negative entry)
# from a cache miss.
_MISSING = object()


def ui_font(size: int = 13, weight: str = "normal") -> tuple:
    family = "PingFang SC" if sys.platform == "darwin" else ("Microsoft YaHei" if sys.platform == "win32" else "Noto Sans CJK SC")
    return (family, size, weight) if weight != "normal" else (family, size)


def _truncate_ui_text(value: object, max_chars: int) -> str:
    """限制单行字段长度，避免表格列之间发生覆盖。"""
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return "…"
    return text[: max_chars - 1] + "…"


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
    # Canvas "units" for one wheel notch (~1/10 of the viewport), so a Windows
    # 120-delta notch and a macOS notch move the same distance.
    WHEEL_UNITS = 1

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
        """Scroll one notch in the direction the wheel really turned.

        Tk reports ``event.num == '??'`` for ``<MouseWheel>`` on Windows/macOS
        and only uses 4/5 for the X11 buttons, so the button test must be an
        exact match: truth-testing ``num`` made ``'??'`` look like a button and
        every notch scrolled *down*. Only the sign of ``delta`` is used -- a
        fixed step keeps a Windows notch (120) and a macOS notch from jumping
        wildly different distances.
        """
        num = getattr(event, "num", 0)
        if num == 4:
            up = True
        elif num == 5:
            up = False
        else:
            raw = int(getattr(event, "delta", 0) or 0)
            if raw == 0:
                return "break"
            # Both Windows (+120 per notch) and macOS (+1..) use a positive
            # delta for "scroll up".
            up = raw > 0
        self.yview_scroll(-self.WHEEL_UNITS if up else self.WHEEL_UNITS, "units")
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

    def refresh_row(self, index: int) -> None:
        """Repaint one row after its backing dict was updated in place.

        Background enrichment mutates the same dict objects handed to
        :meth:`set_rows`; this exposes the single-row redraw so a late cover can
        land without rebuilding the whole list.
        """
        self._redraw_index(index)

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
