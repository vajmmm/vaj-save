import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Union

from .app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .artwork import (
    LLM_PROTOCOLS,
    PLACEHOLDER,
    ArtworkLoader,
    fill_from_preset,
    get_llm_preset,
    llm_preset_keys,
)
from .covers import load_thumbnail
from .identity import (
    STATUS_AMBIGUOUS,
    STATUS_PARTIAL,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    GameIdentity,
    GameIdentityResult,
)
from .library import Snapshot, load_keep_last, parse_keep_last
from .models import SaveEntry, VolumeInfo
from .remote_ftp import DEFAULT_FTP_PORT
from .rom_formats import supported_extensions
from .ui_theme import PLATFORM_COLORS, SWITCH, save_row, status_label
from .ui_widgets import (
    SELECTED_LIST,
    CanvasButton,
    SaveList,
    _rounded_points,
    _truncate_ui_text,
    ui_font,
)
# Re-exported so ``vajsave.app_ui.STATUS_COLORS`` keeps working now that the
# status palette lives next to the widgets that paint it.
from .ui_widgets import STATUS_COLORS  # noqa: F401

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

# Selected surfaces are a light-blue tint of the accent so white rows still read.
SELECTED_ROW = SWITCH["selected"]

# Sentinel for "compute the cover path from the entry" in ``_render_detail_cover``.
_AUTO = object()

# Short, explicit help copy. The archive direction is the important part: the
# app copies saves from the handheld to the computer and never writes back.
HELP_TEXT = (
    "vaj-save 把掌机存档备份到电脑，不会写入掌机。\n\n"
    "· 备份：选中设备上的存档行，点右侧蓝色「备份存档」，"
    "版本会保存到本地备份库。\n"
    "· 恢复：在右侧「版本」里选一个版本，点「恢复」，"
    "版本只会拷贝到你选择的文件夹，不会写入掌机。\n"
    "· FTP：机上开启 FTP 服务器后，点设备区「FTP 拉取」把存档拉到本地缓存再扫描。"
    "默认预设为 Checkpoint，连不上可切换回退预设 ftpd；全程只读，不会写入掌机。\n"
    "· 设置：可修改本地备份库路径、可选 ROM 目录与保留版本数"
    "（0 表示不限制）。"
)


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


def _format_ui_timestamp(value: Optional[str]) -> str:
    """把状态层的 ISO 时间压缩成适合列表和详情栏的显示文本。"""
    if not value:
        return "—"
    return str(value).replace("T", " ")[:16]


def _initial_window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    """Choose a default window size that fits the screen.

    The inspector needs roughly 900px to lay out without crowding, but a fixed
    ``1480x900`` can exceed the usable height of a common 768px display. The
    height is therefore capped to the reported screen height (minus room for the
    OS menu bar / taskbar), while the width keeps the three-column design.
    """
    width = min(1480, max(1180, int(screen_width or 1480) - 80))
    height = min(900, max(560, int(screen_height or 900) - 140))
    return width, height


def _rom_filetypes(platform: str) -> List[tuple[str, str]]:
    """File-dialog filters derived from the canonical ROM extension registry."""
    extensions = supported_extensions(platform)
    if not extensions:
        return [("所有文件", "*.*")]
    patterns = " ".join(f"*{extension}" for extension in extensions)
    return [(f"{platform.upper()} ROM", patterns), ("所有文件", "*.*")]


_IDENTITY_STATUS_LABELS = {
    STATUS_RESOLVED: "已识别",
    STATUS_PARTIAL: "部分识别",
    STATUS_AMBIGUOUS: "多个候选",
    STATUS_UNRESOLVED: "未识别",
}

# Inspector title: ~two lines at 15pt in a 240px wrap. Longer names must not
# grow the right column and shove the status bar off-screen.
_DETAIL_NAME_MAX_CHARS = 28


def save_display(
    state: AppState,
    save: SaveEntry,
    result: Optional[GameIdentityResult] = None,
    *,
    metadata=None,
) -> Dict[str, str]:
    """List/inspector text for one save, including GameIdentity when resolved.

    ``result`` lets a caller that already resolved the save reuse that result
    instead of matching/hashing the ROMs a second time.  ``metadata`` (a
    :class:`~vajsave.metadata.GameMetadata`) promotes the canonical index title
    to the display title once it is known; without it the scanned file name is
    shown so the UI never has to block on the index.
    """
    filename = save.display_name or Path(save.path).name
    if result is None:
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
    canonical_title = getattr(metadata, "canonical_title", "") if metadata else ""
    hint = ""
    if result.status == STATUS_UNRESOLVED and save.platform in ("gba", "nds"):
        hint = "未匹配到 ROM，可在设置中指定 ROM 目录"
    elif result.status == STATUS_AMBIGUOUS:
        hint = "匹配到多个 ROM，未自动选择"
    elif result.reason and result.status == STATUS_PARTIAL:
        hint = result.reason
    return {
        "title": canonical_title or filename,
        "subtitle": " · ".join(subtitle_bits),
        "title_id": title_id or "—",
        "identity_status": identity_status,
        "hint": hint,
    }


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


class VajSaveApp:
    def __init__(
        self,
        root: Optional[tk.Tk] = None,
        state: Optional[AppState] = None,
        loader: Optional[ArtworkLoader] = None,
    ) -> None:
        self.root = root or tk.Tk()
        self.state = state or AppState()
        # Off-thread metadata/cover work. In the app ``root.after(0, ...)`` is the
        # only way results are marshalled back onto the Tk main thread; tests can
        # inject a synchronous loader for deterministic assertions.
        self._artwork_loader = loader or ArtworkLoader(
            lambda fn: self.root.after(0, fn), max_workers=2
        )
        self._selected_save: Optional[SaveEntry] = None
        self._selected_snapshot: Optional[Snapshot] = None
        self._saves_index: List[SaveEntry] = []
        self._volumes_index: List[VolumeInfo] = []
        self._versions_index: List[Snapshot] = []
        self._platform_rows: Dict[str, dict] = {}
        self._identity_candidates: List[GameIdentity] = []
        # Enrichment generations: a background result is only applied when its
        # token still matches, so a slow download for a previously selected save
        # can never overwrite the cover/title of the current one.
        self._enrich_token = 0
        self._detail_cover_path: Optional[str] = None
        # Eager list enrichment: every refresh owns a generation and the row
        # objects/indices it produced, so a late result is attributed back to the
        # exact row (by save path) of the refresh it belongs to.
        self._list_generation = 0
        self._rows_by_path: Dict[str, dict] = {}
        self._row_index_by_path: Dict[str, int] = {}
        self._watch_var = tk.BooleanVar(value=True)
        # "仅显示有更新" starts off so every save is listed by default.
        self._hide_unchanged_var = tk.BooleanVar(value=False)
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
        # The archive desk needs enough room for its three semantic columns, but
        # the initial height must stay inside the usable screen area (a 768px
        # display is common) so the bottom status bar is never off-screen.
        width, height = _initial_window_size(
            self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        )
        self.root.minsize(min(1320, width), min(700, height))
        self.root.geometry(f"{width}x{height}")
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

        # Reserve the bottom status bar before the expanding body so a short
        # window can never clip it: pack order here guarantees its space.
        self._build_bottombar()

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
        self.help_button = CanvasButton(
            top_tools, text="帮助", command=self.on_help_clicked, height=30, padding=10
        )
        self.help_button.pack(side=tk.RIGHT, padx=(6, 0))
        self.settings_button = CanvasButton(
            top_tools, text="设置", command=self.on_settings_clicked, height=30, padding=10
        )
        self.settings_button.pack(side=tk.RIGHT)

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
        # Entry point for every other attached drive (fixed disks included); the
        # list itself only ever shows the active device by default.
        self._other_devices_button = CanvasButton(
            device_head, text="其他设备", command=self.on_other_devices_clicked, height=28, padding=8
        )
        self._other_devices_button.pack(side=tk.RIGHT, padx=(0, 6))
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
        device_actions = tk.Frame(left, bg=PANEL_BG)
        device_actions.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 12))
        self._add_device_button = CanvasButton(
            device_actions, text="＋ 添加设备", command=self.on_open_folder_clicked, height=28, padding=8
        )
        self._add_device_button.pack(side=tk.LEFT)
        # Pull saves straight from a console FTP server (Checkpoint default,
        # ftpd fallback) instead of a mounted card.
        self.ftp_button = CanvasButton(
            device_actions, text="FTP 拉取", command=self.on_ftp_clicked, height=28, padding=8
        )
        self.ftp_button.pack(side=tk.RIGHT)
        library_frame = tk.Frame(left, bg=PANEL_ALT, highlightbackground=BORDER_SOFT, highlightthickness=1)
        library_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=16)
        library_head = tk.Frame(library_frame, bg=PANEL_ALT)
        library_head.pack(fill=tk.X, padx=12, pady=(12, 4))
        tk.Label(library_head, text="本地存档库", bg=PANEL_ALT, fg=INK, font=ui_font(11, "bold")).pack(side=tk.LEFT)
        CanvasButton(library_head, text="打开", command=self.on_open_library_clicked, height=28, padding=8).pack(side=tk.RIGHT)
        # Independent library browse mode: the middle table shows one row per
        # catalog game instead of the current device's saves.
        self._library_browse_button = CanvasButton(
            library_head, text="浏览", command=self.on_browse_library_clicked, height=28, padding=8
        )
        self._library_browse_button.pack(side=tk.RIGHT, padx=(0, 6))
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
        primary = CanvasButton(action_area, text="备份存档", command=self.on_primary_clicked, variant="accent", height=40, padding=12)
        primary.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._primary_button = primary
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

        # Contextual game-identity binding: hidden until the selected save is
        # ambiguous (pick a candidate ROM) or an unresolved GBA/NDS save (pick a
        # ROM file by hand). The frame is gridded/removed in place so the rest of
        # the inspector does not move when it is not needed.
        identity = tk.Frame(right, bg=PANEL_BG)
        identity.grid(row=3, column=0, sticky="ew", padx=20, pady=(0, 10))
        identity.grid_columnconfigure(0, weight=1)
        self.identity_frame = identity
        identity_head = tk.Frame(identity, bg=PANEL_BG)
        identity_head.grid(row=0, column=0, sticky="ew")
        tk.Label(identity_head, text="游戏身份", bg=PANEL_BG, fg=INK, font=ui_font(12, "bold")).pack(side=tk.LEFT)
        self.identity_state_var = tk.StringVar(value="")
        tk.Label(identity_head, textvariable=self.identity_state_var, bg=PANEL_BG, fg=MUTED_STRONG, font=ui_font(10)).pack(side=tk.RIGHT)
        self.identity_hint_var = tk.StringVar(value="")
        tk.Label(
            identity,
            textvariable=self.identity_hint_var,
            bg=PANEL_BG,
            fg=MUTED_STRONG,
            font=ui_font(10),
            wraplength=380,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.identity_list = tk.Listbox(
            identity,
            bg=CARD,
            fg=INK,
            selectbackground=SELECTED_LIST,
            selectforeground=INK,
            font=ui_font(10),
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER_SOFT,
            bd=0,
            activestyle="none",
            selectborderwidth=0,
            height=3,
            exportselection=False,
        )
        self.identity_list.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        identity_actions = tk.Frame(identity, bg=PANEL_BG)
        identity_actions.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._bind_candidate_button = CanvasButton(
            identity_actions, text="绑定所选 ROM", command=self.on_bind_candidate_clicked
        )
        self._manual_rom_button = CanvasButton(
            identity_actions, text="手动选择 ROM…", command=self.on_manual_rom_clicked
        )
        identity.grid_remove()

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

        self.vol_tree = self.vol_list
        self.save_tree = self.save_list
        self.version_tree = self.version_list
        self._build_platform_rows()

    def _build_bottombar(self) -> None:
        """Create the bottom status bar and pack it against the bottom edge.

        Called before the body is packed so the bar always reserves its 42px
        regardless of how short the window is (and whether the identity frame is
        showing), instead of being pushed off-screen by the expanding body.
        """
        bottombar = tk.Frame(self.root, bg=APP_BG, height=42, highlightbackground=BORDER_SOFT, highlightthickness=1)
        bottombar.pack(side=tk.BOTTOM, fill=tk.X)
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
        self._hide_unchanged_button = CanvasButton(self.statusrow, text="仅显示有更新", command=self.on_hide_unchanged_button_clicked, height=30, padding=9)
        self._hide_unchanged_button.pack(side=tk.RIGHT, padx=(0, 6))
        self._hide_unchanged_button.set_selected(bool(self._hide_unchanged_var.get()))
        self._bottom_buttons.append(self._hide_unchanged_button)

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
        """CanvasButton for "仅显示有更新": flip the filter, then run the shared handler."""
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
        self.update_status("仅显示有更新" if self.state.hide_unchanged else "显示全部存档")

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
            self._collapse_device_list()
            self._sync_library_browse_button()
            self.refresh_volumes_ui(select_path=Path(chosen_dir))
            self.refresh_platform_ui()
            self.refresh_saves_ui()
            self.refresh_stats()

    def on_ftp_clicked(self) -> None:
        """Open the FTP pull dialog; Checkpoint is the default preset."""
        dialog = tk.Toplevel(self.root)
        dialog.title("FTP 拉取存档")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)

        tk.Label(
            dialog, text="从掌机 FTP 拉取存档（只读）", bg=BG, fg=INK, font=ui_font(14, "bold")
        ).pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(
            dialog,
            text="默认使用 Checkpoint 预设；连不上可切换回退预设 ftpd。"
            "存档只拉取到本地缓存，不会写入掌机。",
            bg=BG,
            fg=TEXT,
            font=ui_font(11),
            justify=tk.LEFT,
            anchor="w",
            wraplength=360,
        ).pack(anchor="w", padx=16, pady=(0, 8))

        preset_var = tk.StringVar(value=self.state.ftp_preset_key)
        dialog.ftp_preset_var = preset_var
        preset_row = tk.Frame(dialog, bg=BG)
        preset_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(preset_row, text="预设", bg=BG, fg=TEXT, font=ui_font(12, "bold")).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        preset_buttons: Dict[str, CanvasButton] = {}

        def choose_preset(key: str) -> None:
            preset_var.set(key)
            for preset_key, button in preset_buttons.items():
                button.set_selected(preset_key == key)

        for preset in self.state.ftp_presets():
            button = CanvasButton(
                preset_row,
                text=preset.label,
                command=lambda preset_key=preset.key: choose_preset(preset_key),
                height=28,
                padding=10,
            )
            button.pack(side=tk.LEFT, padx=(0, 6))
            preset_buttons[preset.key] = button
        choose_preset(self.state.ftp_preset_key)
        # Exposed so tests can assert the offered presets and the default.
        dialog.ftp_preset_buttons = preset_buttons

        host_var = tk.StringVar(value=self.state.ftp_host)
        port_var = tk.StringVar(value=str(self.state.ftp_port or DEFAULT_FTP_PORT))
        user_var = tk.StringVar(value=self.state.ftp_user)
        password_var = tk.StringVar()
        dialog.ftp_host_var = host_var
        dialog.ftp_port_var = port_var
        dialog.ftp_user_var = user_var
        dialog.ftp_password_var = password_var

        def add_field(label: str, var: tk.StringVar, *, show: Optional[str] = None) -> None:
            row = tk.Frame(dialog, bg=BG)
            row.pack(fill=tk.X, padx=16, pady=(6, 0))
            tk.Label(row, text=label, bg=BG, fg=TEXT, font=ui_font(11)).pack(
                side=tk.LEFT, padx=(0, 8)
            )
            entry = ttk.Entry(row, textvariable=var, width=18)
            if show:
                entry.configure(show=show)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        add_field("主机", host_var)
        add_field("端口", port_var)
        add_field("用户", user_var)
        add_field("密码", password_var, show="\u2022")

        def pull() -> None:
            self.state.configure_ftp(
                host=host_var.get().strip(),
                port=port_var.get().strip(),
                user=user_var.get().strip(),
                password=password_var.get(),
                preset_key=preset_var.get(),
            )
            dialog.destroy()
            self.state.pull_ftp_saves()
            self._collapse_device_list()
            self._sync_library_browse_button()
            self.refresh_volumes_ui(select_path=self.state.current_mount)
            self.refresh_platform_ui()
            self.refresh_saves_ui()
            self.refresh_stats()

        # Exposed so tests can drive the dialog without a real click.
        dialog.ftp_pull = pull

        btn_row = tk.Frame(dialog, bg=BG)
        btn_row.pack(fill=tk.X, padx=16, pady=16)
        CanvasButton(btn_row, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        CanvasButton(btn_row, text="拉取", variant="accent", command=pull).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        dialog.grab_set()

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
        # Picking a device collapses the list back to just that device and leaves
        # library browse mode if it was active.
        self._collapse_device_list()
        self.refresh_volumes_ui(select_path=volume.mount_point)
        self.refresh_platform_ui()
        self.refresh_saves_ui()
        self.refresh_stats()

    def on_other_devices_clicked(self) -> None:
        """Toggle the device list between the active device and every volume."""
        self._show_all_devices = not getattr(self, "_show_all_devices", False)
        button = getattr(self, "_other_devices_button", None)
        if button is not None:
            button.set_selected(bool(self._show_all_devices))
        self.refresh_volumes_ui(select_path=self.state.current_mount)
        if self._show_all_devices and not self.state.volumes:
            self.update_warning("没有检测到可选择的设备")
        else:
            self.update_warning("")

    def _collapse_device_list(self) -> None:
        self._show_all_devices = False
        button = getattr(self, "_other_devices_button", None)
        if button is not None:
            button.set_selected(False)

    def on_browse_library_clicked(self) -> None:
        """Enter/leave the independent local library browse mode."""
        self.state.set_library_mode(not self.state.library_mode)
        self._collapse_device_list()
        self._sync_library_browse_button()
        self.refresh_volumes_ui(select_path=self.state.current_mount)
        self.refresh_platform_ui()
        self.refresh_saves_ui()
        self.refresh_stats()
        self.update_warning("")

    def _sync_library_browse_button(self) -> None:
        button = getattr(self, "_library_browse_button", None)
        if button is None:
            return
        button.set_text("返回设备" if self.state.library_mode else "浏览")
        button.set_selected(bool(self.state.library_mode))

    def _clear_detail(self) -> None:
        # Invalidate any in-flight enrichment so its result cannot repaint a
        # cover/title for a save that is no longer selected.
        self._enrich_token += 1
        self._detail_cover_path = None
        self.detail_name.set("未选择游戏")
        self.detail_subtitle_var.set("")
        for var in self.detail_vars.values():
            var.set("—")
        self.detail_hint_var.set("")
        self.note_var.set("")
        self._hide_identity_section()
        self._render_detail_cover(None)

    def _render_detail_cover(
        self, save: Optional[SaveEntry], *, cover_path=_AUTO
    ) -> None:
        """绘制当前存档封面，且不让详情面板依赖列表缓存。

        ``cover_path=_AUTO`` resolves the network-free best cover (user local >
        downloaded cache > embedded) synchronously; a background enrichment can
        later pass an explicit path to upgrade to a freshly downloaded image.
        """
        canvas = getattr(self, "detail_cover_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        self.detail_cover_ref = None
        if cover_path is _AUTO:
            resolution = self.state.resolve_save_cover(save) if save else PLACEHOLDER
            cover_path = resolution.path
        self._detail_cover_path = str(cover_path) if cover_path else None
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
        result = self.state.resolve_save_identity(save)
        # Cache-only metadata keeps the first paint instant; a background pass
        # fills in the canonical title (and cover) once the index answers.
        metadata = self.state.cached_save_metadata(result.identity)
        view = save_display(self.state, save, result=result, metadata=metadata)
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
        self._update_identity_section(save, result)
        self.note_var.set(self.state.game_note(save))
        self.refresh_versions_ui()
        self._request_enrichment(save, result)

    # -- background metadata / cover enrichment -----------------------------

    def _submit_enrichment(
        self,
        save: SaveEntry,
        result: Optional[GameIdentityResult],
        callback,
    ) -> None:
        """Run the shared metadata + cover task for ``save`` off the UI thread.

        Both enrichment entry points (the inspector and the eager list) funnel
        through here, so they reuse one worker task -- keyed by ``save.path`` and
        coalesced by :class:`ArtworkLoader` -- instead of duplicating the work.
        Staleness stays the caller's job: each callback carries its own token or
        generation and drops results that no longer apply.
        """
        identity = result.identity if result is not None else None
        if identity is None:
            return

        def task():
            metadata = self.state.resolve_save_metadata(save, identity)
            cover = self.state.ensure_save_cover(save, result, metadata)
            return metadata, cover

        try:
            self._artwork_loader.submit(save.path, task, callback)
        except Exception:  # noqa: BLE001 - enrichment is strictly best-effort
            pass

    def _request_enrichment(
        self, save: SaveEntry, result: Optional[GameIdentityResult]
    ) -> None:
        """Resolve metadata + cover for ``save`` off the UI thread.

        The generation token makes a late result a no-op once the selection has
        moved on, and the loader coalesces repeat requests for the same path so
        one save never triggers duplicate work or duplicate repaints.
        """
        self._enrich_token += 1
        token = self._enrich_token
        if self.state.library_mode or result is None or result.identity is None:
            return

        def callback(payload) -> None:
            if token != self._enrich_token:
                return  # stale: the selection changed while we were working
            self._apply_enrichment(save, payload)

        self._submit_enrichment(save, result, callback)

    def _apply_enrichment(self, save: SaveEntry, payload) -> None:
        """Apply a finished background result, if it still belongs to the view."""
        if not payload or save is not self._selected_save:
            return
        metadata, cover = payload
        if metadata is not None and getattr(metadata, "canonical_title", ""):
            self.detail_name.set(
                _truncate_ui_text(metadata.canonical_title, _DETAIL_NAME_MAX_CHARS)
            )
        path = getattr(cover, "path", None)
        if path and not getattr(cover, "is_placeholder", False) and path != self._detail_cover_path:
            self._render_detail_cover(save, cover_path=path)

    def _schedule_list_enrichment(
        self,
        saves: List[SaveEntry],
        result_by_path: Dict[str, GameIdentityResult],
        generation: int,
    ) -> None:
        """Kick off metadata/cover work for every resolved visible save.

        ``generation`` is the list refresh this pass belongs to: a result whose
        generation is no longer current is dropped instead of painting a row of a
        newer list.  Unresolved/ambiguous saves are skipped so the app never
        guesses a cover name (and never downloads) for an unknown game.
        """
        if self.state.library_mode:
            return
        for save in saves:
            result = result_by_path.get(save.path)
            if result is None or not result.is_resolved or result.identity is None:
                continue
            self._submit_row_enrichment(save, result, generation)

    def _submit_row_enrichment(
        self, save: SaveEntry, result: GameIdentityResult, generation: int
    ) -> None:
        def callback(payload) -> None:
            if generation != self._list_generation:
                return  # stale: a newer refresh already replaced this list
            self._apply_row_enrichment(save, result, payload)

        self._submit_enrichment(save, result, callback)

    def _apply_row_enrichment(
        self, save: SaveEntry, result: GameIdentityResult, payload
    ) -> None:
        """Apply a finished background result to its list row (if still present)."""
        if not payload:
            return
        index = self._row_index_by_path.get(save.path)
        row = self._rows_by_path.get(save.path)
        if row is None or index is None:
            return
        metadata, cover = payload
        changed = False
        if metadata is not None and getattr(metadata, "canonical_title", ""):
            view = save_display(self.state, save, result=result, metadata=metadata)
            if row.get("title") != view["title"]:
                row["title"] = view["title"]
                changed = True
        path = getattr(cover, "path", None)
        if path and not getattr(cover, "is_placeholder", False) and row.get("cover") != path:
            row["cover"] = path
            changed = True
        if changed:
            self.save_list.refresh_row(index)
        if save is self._selected_save:
            self._apply_enrichment(save, payload)

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

    def on_primary_clicked(self) -> None:
        """Run the inspector's primary action for the current browse source."""
        if self.state.library_mode:
            self.on_delete_backup_clicked()
        else:
            self.on_backup_clicked()

    def _sync_primary_action(self) -> None:
        """Keep the primary button's label/variant in step with the source.

        Browsing a device offers the blue 「备份存档」 action; browsing the local
        library offers a neutral 「删除备份」 that never copies the catalog into
        its own tree. The neutral face keeps the saturated accent reserved for
        the backup action, per the design spec.
        """
        button = getattr(self, "_primary_button", None)
        if button is None:
            return
        library_mode = bool(self.state.library_mode)
        button.variant = "neutral" if library_mode else "accent"
        button.accent = not library_mode
        button.set_text("删除备份" if library_mode else "备份存档")

    def _confirm_delete_backup(self, entries: List[SaveEntry]) -> bool:
        """Ask before deleting; a cancel returns False so nothing is touched."""
        names = "、".join(
            (entry.display_name or entry.title_id or entry.path) for entry in entries[:3]
        )
        if len(entries) > 3:
            names += f" 等 {len(entries)} 款"
        return bool(
            messagebox.askyesno(
                "删除本地备份",
                f"确定删除「{names}」的全部本地备份及其封面吗？\n此操作不会影响设备上的存档。",
                parent=self.root,
            )
        )

    def on_delete_backup_clicked(self) -> None:
        """Delete the selected library games after an explicit confirmation."""
        if not self.state.library_mode:
            return
        chosen = self._selected_saves()
        if not chosen:
            self.update_warning("先选择要删除的备份")
            return
        if not self._confirm_delete_backup(chosen):
            return
        results = [self.state.delete_library_game(entry) for entry in chosen]
        removed = sum(1 for result in results if result.ok)
        failed = len(results) - removed
        self.refresh_saves_ui()
        self.refresh_versions_ui()
        self.refresh_stats()
        if failed:
            # A partial failure must never read as a full success.
            self.update_status(f"删除完成 {removed} 款，{failed} 款未完成")
            self.update_warning("部分备份未能删除，请重试")
        else:
            self.update_status(f"已删除 {removed} 款游戏的本地备份")
            self.update_warning("")

    def on_backup_clicked(self) -> None:
        """Back up exactly the rows that are currently selected."""
        if self.state.library_mode:
            # The listed source is the library itself; backing it up would copy
            # the catalog into its own tree.
            self.update_warning("本地存档库无需备份")
            return
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
        confirmed = messagebox.askyesno(
            "恢复版本",
            "将把所选版本拷贝到你选择的文件夹，不会写入掌机。\n继续吗？",
            parent=self.root,
        )
        if not confirmed:
            return
        chosen = filedialog.askdirectory(title="恢复到哪个文件夹？", parent=self.root)
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

    # -- game identity binding ---------------------------------------------

    @staticmethod
    def _candidate_label(identity: GameIdentity) -> str:
        name = identity.title or identity.identity_key
        if identity.rom_path:
            name = f"{name}  ·  {Path(identity.rom_path).name}"
        return _truncate_ui_text(name, 60)

    def _show_identity_frame(self) -> None:
        frame = getattr(self, "identity_frame", None)
        if frame is not None and not frame.winfo_manager():
            frame.grid()

    def _hide_identity_section(self) -> None:
        self._identity_candidates = []
        frame = getattr(self, "identity_frame", None)
        if frame is not None and frame.winfo_manager():
            frame.grid_remove()

    def _update_identity_section(self, save: Optional[SaveEntry], result: Optional[GameIdentityResult]) -> None:
        """Show the contextual ROM binding controls for the selected save.

        Ambiguous saves list their candidate ROMs; unresolved GBA/NDS saves get a
        manual "选择 ROM" file dialog. Every other save hides the section.
        """
        if getattr(self, "identity_frame", None) is None:
            return
        if save is None or result is None:
            self._hide_identity_section()
            return
        platform = (getattr(save, "platform", "") or "").lower()
        if result.status == STATUS_AMBIGUOUS and result.candidates:
            self._identity_candidates = list(result.candidates)
            self.identity_state_var.set("多个候选")
            self.identity_hint_var.set("匹配到多个 ROM，选择要绑定的游戏身份")
            self.identity_list.delete(0, tk.END)
            for candidate in self._identity_candidates:
                self.identity_list.insert(tk.END, self._candidate_label(candidate))
            self.identity_list.selection_clear(0, tk.END)
            self.identity_list.selection_set(0)
            self.identity_list.grid()
            self._manual_rom_button.pack_forget()
            if not self._bind_candidate_button.winfo_manager():
                self._bind_candidate_button.pack(side=tk.LEFT)
            self._show_identity_frame()
        elif result.status == STATUS_UNRESOLVED and platform in ("gba", "nds"):
            self._identity_candidates = []
            self.identity_state_var.set("未识别")
            self.identity_hint_var.set("未匹配到 ROM，可手动选择该存档对应的 ROM 文件")
            self.identity_list.delete(0, tk.END)
            self.identity_list.grid_remove()
            self._bind_candidate_button.pack_forget()
            if not self._manual_rom_button.winfo_manager():
                self._manual_rom_button.pack(side=tk.LEFT)
            self._show_identity_frame()
        else:
            self._hide_identity_section()

    def on_bind_candidate_clicked(self) -> None:
        """Bind the candidate ROM the user picked from the ambiguous list."""
        save = self._selected_save
        if save is None:
            return
        selection = self.identity_list.curselection()
        if not selection or selection[0] >= len(self._identity_candidates):
            self.update_warning("先选择一个候选 ROM")
            return
        self._bind_identity(save, identity=self._identity_candidates[selection[0]])

    def on_manual_rom_clicked(self) -> None:
        """Pick a ROM file by hand for an unresolved GBA/NDS save."""
        save = self._selected_save
        if save is None:
            return
        platform = (getattr(save, "platform", "") or "").lower()
        chosen = filedialog.askopenfilename(
            title="选择 ROM 文件",
            filetypes=_rom_filetypes(platform),
        )
        if not chosen:
            return
        self._bind_identity(save, rom_path=chosen)

    def _bind_identity(self, save: SaveEntry, *, identity=None, rom_path=None) -> None:
        """Persist a manual binding, then refresh the list/inspector in place."""
        try:
            bound = self.state.bind_save_identity(save, identity=identity, rom_path=rom_path)
        except Exception as e:  # noqa: BLE001 - a bad ROM file must not crash the app
            self.update_warning(f"绑定失败: {e}")
            return
        title = bound.title or bound.identity_key
        self.state.status_text = f"已绑定游戏身份: {title}"
        self.update_warning("")
        self.refresh_saves_ui()

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

    def _apply_libretro_dir(self, libretro_dir) -> None:
        """Apply the optional libretro metadata directory chosen in the settings dialog."""
        self.state.set_libretro_dir(libretro_dir)
        self.refresh_saves_ui()
        self.update_status("元数据目录已更新")

    def _apply_keep_last(self, value: int) -> None:
        """Apply the keep_last (保留版本数) chosen in the settings dialog."""
        if self.state.set_keep_last(value) is None:
            self.update_warning("保留版本数设置失败")
            return
        self.update_status(self.state.status_text)
        self.update_warning("")

    def _apply_llm_cover(
        self,
        enabled: bool,
        api_key: str,
        base_url: str = "",
        model: str = "",
        protocol: str = "",
        preset: str = "",
    ) -> None:
        """Apply the optional LLM cover-disambiguation settings.

        The key is handed to the state, which persists it; it is never echoed
        into the status/warning text shown here. Blank base URL/model fall back
        to the selected protocol's built-in defaults, and switching protocol
        only rewrites a still-default endpoint. ``preset`` records the chosen
        provider preset alongside the fields it filled.
        """
        self.state.set_llm_cover(
            enabled=enabled,
            api_key=api_key,
            base_url=base_url,
            model=model,
            protocol=protocol,
            preset=preset,
        )
        if enabled and not api_key:
            self.update_status("已开启 LLM 封面消歧（未填 API Key，暂不生效）")
        else:
            self.update_status("LLM 封面消歧设置已更新")

    def on_help_clicked(self) -> None:
        """Open the short guide; the archive direction is the point."""
        dialog = tk.Toplevel(self.root)
        dialog.title("帮助")
        dialog.configure(bg=BG)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        tk.Label(dialog, text="使用说明", bg=BG, fg=INK, font=ui_font(15, "bold")).pack(
            anchor="w", padx=16, pady=(16, 6)
        )
        tk.Label(
            dialog,
            text=HELP_TEXT,
            bg=BG,
            fg=TEXT,
            font=ui_font(12),
            justify=tk.LEFT,
            anchor="w",
            wraplength=380,
        ).pack(anchor="w", padx=16)
        CanvasButton(dialog, text="关闭", command=dialog.destroy).pack(
            side=tk.RIGHT, padx=16, pady=16
        )
        dialog.grab_set()

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
        meta_var = add_dir_row(
            "Libretro 元数据目录（可选）",
            str(self.state.libretro_dir or ""),
            "选择包含 libretro/No-Intro .dat 的目录",
        )

        keep_var = tk.StringVar(value=str(load_keep_last(self.state.library_root)))
        tk.Label(
            dialog,
            text="保留版本数（0 表示不限制）",
            bg=BG,
            fg=TEXT,
            font=ui_font(13, "bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))
        keep_row = tk.Frame(dialog, bg=BG)
        keep_row.pack(fill=tk.X, padx=16)
        ttk.Entry(keep_row, textvariable=keep_var, width=12).pack(side=tk.LEFT)
        # Exposed so the dialog's bound value is reachable from tests.
        dialog.keep_last_var = keep_var

        # Optional LLM cover disambiguation: off by default, only consulted for
        # a genuinely ambiguous listing, and inert without a key.
        tk.Label(
            dialog,
            text="LLM 封面消歧（可选，仅多候选歧义时）",
            bg=BG,
            fg=TEXT,
            font=ui_font(13, "bold"),
        ).pack(anchor="w", padx=16, pady=(12, 6))
        llm_row = tk.Frame(dialog, bg=BG)
        llm_row.pack(fill=tk.X, padx=16)
        llm_toggle = CanvasButton(
            llm_row,
            text="启用",
            selected=self.state.llm_cover_enabled,
            command=lambda: llm_toggle.set_selected(not llm_toggle.selected),
        )
        llm_toggle.pack(side=tk.LEFT)
        dialog.llm_cover_button = llm_toggle

        # The key needs its own labelled row: sharing the toggle's row left the
        # masked entry unlabelled, so users could not tell where to paste it.
        llm_key_var = tk.StringVar(value=self.state.llm_api_key)
        tk.Label(dialog, text="API Key", bg=BG, fg=TEXT, font=ui_font(12)).pack(
            anchor="w", padx=16, pady=(8, 2)
        )
        llm_key_entry = ttk.Entry(dialog, textvariable=llm_key_var)
        llm_key_entry.configure(show="\u2022")
        llm_key_entry.pack(fill=tk.X, padx=16)
        dialog.llm_api_key_var = llm_key_var
        dialog.llm_api_key_entry = llm_key_entry

        # Persistent endpoint/model for the chooser; blank input falls back to
        # the built-in defaults. The protocol selector swaps a still-default
        # endpoint, so the field tracks the chosen OpenAI/Anthropic endpoint.
        #
        # Provider preset: choosing one auto-fills the protocol/base/model
        # fields from the preset, but only where the field still holds the
        # previous preset's value -- a customised endpoint/model is preserved.
        llm_preset_var = tk.StringVar(value=self.state.llm_preset)
        tk.Label(dialog, text="服务商预设", bg=BG, fg=TEXT, font=ui_font(12)).pack(
            anchor="w", padx=16, pady=(8, 2)
        )
        llm_preset_combo = ttk.Combobox(
            dialog,
            textvariable=llm_preset_var,
            values=list(llm_preset_keys()),
            state="readonly",
        )
        llm_preset_combo.pack(fill=tk.X, padx=16)

        llm_protocol_var = tk.StringVar(value=self.state.llm_protocol)
        tk.Label(dialog, text="协议", bg=BG, fg=TEXT, font=ui_font(12)).pack(
            anchor="w", padx=16, pady=(8, 2)
        )
        llm_protocol_combo = ttk.Combobox(
            dialog,
            textvariable=llm_protocol_var,
            values=list(LLM_PROTOCOLS),
            state="readonly",
        )
        llm_protocol_combo.pack(fill=tk.X, padx=16)
        llm_base_var = tk.StringVar(value=self.state.llm_base_url)
        tk.Label(dialog, text="Base URL", bg=BG, fg=TEXT, font=ui_font(12)).pack(
            anchor="w", padx=16, pady=(8, 2)
        )
        ttk.Entry(dialog, textvariable=llm_base_var).pack(fill=tk.X, padx=16)
        llm_model_var = tk.StringVar(value=self.state.llm_model)
        tk.Label(dialog, text="模型", bg=BG, fg=TEXT, font=ui_font(12)).pack(
            anchor="w", padx=16, pady=(8, 2)
        )
        ttk.Entry(dialog, textvariable=llm_model_var).pack(fill=tk.X, padx=16)

        # The preset the fields currently track, updated as the user switches
        # presets so consecutive selections chain correctly.
        active_preset = [self.state.llm_preset]

        def select_preset(_event: object = None) -> None:
            """Auto-fill protocol/base/model from the chosen provider preset."""
            target = get_llm_preset(llm_preset_var.get())
            source = get_llm_preset(active_preset[0])
            protocol, base, model = fill_from_preset(
                target,
                source,
                llm_protocol_var.get(),
                llm_base_var.get(),
                llm_model_var.get(),
            )
            llm_protocol_var.set(protocol)
            llm_base_var.set(base)
            llm_model_var.set(model)
            active_preset[0] = target.key

        llm_preset_combo.bind("<<ComboboxSelected>>", select_preset)
        dialog.llm_protocol_var = llm_protocol_var
        dialog.llm_protocol_combo = llm_protocol_combo
        dialog.llm_base_url_var = llm_base_var
        dialog.llm_model_var = llm_model_var
        dialog.llm_preset_var = llm_preset_var
        dialog.llm_preset_combo = llm_preset_combo
        dialog.llm_preset_select = select_preset

        def save() -> None:
            chosen = path_var.get().strip()
            if not chosen:
                return
            keep_value = parse_keep_last(keep_var.get())
            if keep_value is None:
                self.update_warning("保留版本数需为不小于 0 的整数")
                return
            gba = gba_var.get().strip()
            nds = nds_var.get().strip()
            meta = meta_var.get().strip()
            dialog.destroy()
            self._apply_library_root(chosen)
            self._apply_rom_dirs(gba or None, nds or None)
            self._apply_libretro_dir(meta or None)
            self._apply_keep_last(keep_value)
            self._apply_llm_cover(
                dialog.llm_cover_button.selected,
                llm_key_var.get().strip(),
                llm_base_var.get().strip(),
                llm_model_var.get().strip(),
                llm_protocol_var.get().strip(),
                llm_preset_var.get().strip(),
            )

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
        if getattr(self, "_show_all_devices", False):
            # Explicit "other devices" view: every attached volume is choosable.
            display = list(self.state.volumes)
        elif target is not None:
            # Default view: only the active device is presented.
            display = [
                vol for vol in self.state.volumes if Path(vol.mount_point) == Path(target)
            ]
        else:
            display = []
        selected = None
        for index, vol in enumerate(display):
            mount_name = Path(vol.mount_point).name or str(vol.mount_point)
            self.vol_list.insert(tk.END, f"●  {vol.name}   {mount_name}")
            self._volumes_index.append(vol)
            if target and Path(vol.mount_point) == Path(target):
                selected = index
        if selected is not None:
            self.vol_list.selection_set(selected)
        self.update_status(self.state.status_text)

    def refresh_saves_ui(self) -> None:
        self._list_generation += 1
        generation = self._list_generation
        # The inspector's primary action depends on the browse source. Device ->
        # 「备份存档」, local library -> 「删除备份」.
        self._sync_primary_action()
        self._rows_by_path = {}
        self._row_index_by_path = {}
        previous = self._selected_save
        self._selected_save = None
        self._saves_index = []
        rows: List[dict] = []
        restore_index: Optional[int] = None
        groups = self.state.grouped_saves()
        # Resolve every visible save in one batch: the ROM identity cache then
        # flushes once for the whole list instead of after every row.
        visible = [save for _group_name, saves in groups for save in saves]
        resolved = self.state.resolve_identities(visible)
        result_by_path = {save.path: result for save, result in zip(visible, resolved)}
        for _group_name, saves in groups:
            for save in saves:
                status = self.state.save_status(save)
                row = save_row(save, status, starred=self.state.is_starred(save))
                identity = result_by_path.get(save.path)
                identity_obj = identity.identity if identity is not None else None
                metadata = self.state.cached_save_metadata(identity_obj)
                view = save_display(self.state, save, result=identity, metadata=metadata)
                row["title"] = view["title"]
                row["subtitle"] = view["subtitle"]
                row["cover"] = self.state.resolve_save_cover(save, result=identity).path
                row["platform_label"] = PLATFORM_LABELS.get(save.platform, save.platform)
                row["title_id"] = view["title_id"]
                row["last_backup"] = _format_ui_timestamp(status.last_backup_at)
                row["version_count"] = len(self.state.versions_for_entry(save))
                index = len(rows)
                rows.append(row)
                self._saves_index.append(save)
                self._rows_by_path[save.path] = row
                self._row_index_by_path[save.path] = index
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
        # After the first paint, enrich *every* resolved row in the background so
        # the whole list fills its covers, not just the selected inspector row.
        # Library rows already carry their identity and metadata, so browsing the
        # catalog must not trigger ROM lookups or network cover downloads.
        if not self.state.library_mode:
            self._schedule_list_enrichment(visible, result_by_path, generation)

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
            source = "本地存档库" if self.state.library_mode else "设备"
            self.archive_hint_var.set(
                f"{source} · 共 {len(self.state.visible_saves())} 个可见存档 · 按最近备份时间排序"
            )
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
        # Drop any pending enrichment result and stop the worker pool.
        self._enrich_token += 1
        self._list_generation += 1
        loader = getattr(self, "_artwork_loader", None)
        if loader is not None:
            try:
                loader.shutdown(wait=False)
            except Exception:
                pass

    def on_close(self) -> None:
        self._stop_background()
        self.root.destroy()


def build_app(
    state: Optional[AppState] = None,
    root: Optional[tk.Tk] = None,
    loader: Optional[ArtworkLoader] = None,
) -> VajSaveApp:
    return VajSaveApp(root=root, state=state, loader=loader)
