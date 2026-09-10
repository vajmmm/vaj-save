import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional, Union

from .app_state import BACKUP_STATUS_LABELS, PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .library import Snapshot
from .models import SaveEntry, VolumeInfo


BG = "#1c1c1e"
SIDE = "#2c2c2e"
CARD = "#3a3a3c"
TEXT = "#f5f5f7"
MUTED = "#8e8e93"
LINE = "#48484a"
BLUE = "#0a84ff"
GREEN = "#30d158"
ORANGE = "#ff9f0a"
RED = "#ff453a"

PLATFORM_COLORS = {
    "all": BLUE,
    "psp": "#64d2ff",
    "vita": "#63e6be",
    "switch": "#ff453a",
    "3ds": "#ffd60a",
    "nds": "#bf5af2",
    "gba": "#30d158",
}


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
        style.configure("TButton", background=CARD, foreground=TEXT, borderwidth=0, padding=(14, 7), font=ui_font(13))
        style.map("TButton", background=[("active", LINE), ("pressed", SIDE)])
        style.configure("Accent.TButton", background=BLUE, foreground="#fff")
        style.map("Accent.TButton", background=[("active", "#409cff")])
        style.configure("TCheckbutton", background=BG, foreground=TEXT, font=ui_font(13))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT, bordercolor=LINE, padding=6)
        style.configure(
            "Treeview",
            background=SIDE,
            fieldbackground=SIDE,
            foreground=TEXT,
            borderwidth=0,
            rowheight=32,
            font=ui_font(13),
        )
        style.configure("Treeview.Heading", background=SIDE, foreground=MUTED, relief="flat", font=ui_font(12))
        style.map("Treeview", background=[("selected", BLUE)], foreground=[("selected", "#fff")])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def _create_widgets(self) -> None:
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill=tk.X, padx=24, pady=(18, 12))

        titles = tk.Frame(header, bg=BG)
        titles.pack(side=tk.LEFT)
        tk.Label(titles, text="vaj-save", bg=BG, fg=TEXT, font=ui_font(22, "bold")).pack(anchor="w")
        tk.Label(titles, text="把掌机存档备份下来，按版本管理", bg=BG, fg=MUTED, font=ui_font(13)).pack(anchor="w", pady=(2, 0))

        self.stats_var = tk.StringVar(value="")
        tk.Label(header, textvariable=self.stats_var, bg=BG, fg=MUTED, font=ui_font(12)).pack(side=tk.RIGHT)

        toolbar = tk.Frame(self.root, bg=BG)
        toolbar.pack(fill=tk.X, padx=24, pady=(0, 12))
        tk.Label(toolbar, text="搜索", bg=BG, fg=MUTED, font=ui_font(12)).pack(side=tk.LEFT, padx=(0, 8))
        self.search_var = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=self.search_var)
        search.pack(side=tk.LEFT, fill=tk.X, expand=True)
        search.bind("<KeyRelease>", self.on_search)
        ttk.Button(toolbar, text="刷新", command=self.on_refresh_clicked).pack(side=tk.LEFT, padx=(12, 6))
        ttk.Button(toolbar, text="打开文件夹", command=self.on_open_folder_clicked).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="打开本地库", command=self.on_open_library_clicked).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar, text="设置", command=self.on_settings_clicked).pack(side=tk.LEFT, padx=6)
        ttk.Checkbutton(toolbar, text="监听插拔", variable=self._watch_var, command=self.on_watch_toggle).pack(side=tk.LEFT, padx=(12, 0))
        self._hide_unchanged_var.set(bool(self.state.hide_unchanged))
        ttk.Checkbutton(
            toolbar,
            text="隐藏已备份",
            variable=self._hide_unchanged_var,
            command=self.on_hide_unchanged_toggle,
        ).pack(side=tk.LEFT, padx=(12, 0))

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
            selectforeground="#fff",
            font=ui_font(12),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
        )
        self.vol_list.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 16))

        mid = tk.Frame(body, bg=SIDE)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12)
        tk.Label(mid, text="游戏", bg=SIDE, fg=MUTED, font=ui_font(12)).pack(anchor="w", padx=16, pady=(16, 8))
        self.save_list = tk.Listbox(
            mid,
            bg=SIDE,
            fg=TEXT,
            selectbackground=CARD,
            selectforeground=TEXT,
            font=ui_font(14),
            relief="flat",
            highlightthickness=0,
            bd=0,
            activestyle="none",
            selectmode=tk.EXTENDED,
        )
        self.save_list.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 16))

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
        ttk.Button(actions, text="备份", style="Accent.TButton", command=self.on_save_local_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="备份所选", command=self.on_save_selected_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="备份当前列表", command=self.on_save_visible_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="恢复这个版本…", command=self.on_restore_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="导出 ZIP", command=self.on_export_zip_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="收藏", command=self.on_star_clicked).pack(fill=tk.X, pady=3)
        finder = "在访达中显示" if sys.platform == "darwin" else "在文件管理器中显示"
        ttk.Button(actions, text=finder, command=self.on_show_in_finder_clicked).pack(fill=tk.X, pady=3)
        ttk.Button(actions, text="只看收藏", command=self.on_starred_filter).pack(fill=tk.X, pady=3)

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
            selectforeground="#fff",
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

        footer = tk.Frame(self.root, bg=BG)
        footer.pack(fill=tk.X, padx=24, pady=(0, 14))
        self.status_label_var = tk.StringVar(value="准备好了，插上掌机或打开文件夹就可以开始")
        self.status_label = tk.Label(footer, textvariable=self.status_label_var, bg=BG, fg=MUTED, anchor="w", font=ui_font(12))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.warning_label_var = tk.StringVar(value="")
        self.warning_label = tk.Label(footer, textvariable=self.warning_label_var, bg=BG, fg=RED, anchor="e", font=ui_font(12))
        self.warning_label.pack(side=tk.RIGHT)

        self.vol_tree = self.vol_list
        self.save_tree = self.save_list
        self.version_tree = self.version_list
        self._build_platform_rows()

    def _build_platform_rows(self) -> None:
        for child in self.platform_box.winfo_children():
            child.destroy()
        self._platform_rows.clear()
        counts = self.state.platform_counts()
        total = len(self.state.all_saves())
        for key in PLATFORM_ORDER:
            count = total if key == "all" else counts.get(key, 0)
            selected = key == self.state.selected_platform
            row_bg = CARD if selected else SIDE
            row = tk.Frame(self.platform_box, bg=row_bg, cursor="hand2")
            row.pack(fill=tk.X, pady=1)
            pip = tk.Frame(row, bg=PLATFORM_COLORS.get(key, BLUE), width=3)
            pip.pack(side=tk.LEFT, fill=tk.Y)
            name = tk.Label(row, text=PLATFORM_LABELS.get(key, key), bg=row_bg, fg=TEXT, font=ui_font(13), anchor="w")
            name.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10, pady=8)
            badge = tk.Label(row, text=str(count), bg=row_bg, fg=MUTED, font=ui_font(12))
            badge.pack(side=tk.RIGHT, padx=12)
            for widget in (row, pip, name, badge):
                widget.bind("<Button-1>", lambda _e, platform=key: self.on_platform_clicked(platform))
            self._platform_rows[key] = {"row": row, "name": name, "badge": badge, "pip": pip}

    def _bind_events(self) -> None:
        self.vol_list.bind("<<ListboxSelect>>", self.on_volume_selected)
        self.save_list.bind("<<ListboxSelect>>", self.on_save_selected)
        self.version_list.bind("<<ListboxSelect>>", self.on_version_selected)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

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

    def on_hide_unchanged_toggle(self) -> None:
        desired = bool(self._hide_unchanged_var.get())
        if desired != self.state.hide_unchanged:
            self.state.toggle_hide_unchanged()
        self._hide_unchanged_var.set(self.state.hide_unchanged)
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

    def on_watch_toggle(self) -> None:
        if self._watch_var.get():
            self.state.start_watch()
            self.update_status("已开始监听设备插拔")
        else:
            self.state.stop_watch()
            self.update_status("已停止监听")

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
        if not selection or selection[0] >= len(self._saves_index):
            self._selected_save = None
            self.path_entry_var.set("")
            self.detail_name.set("未选择游戏")
            self.detail_meta.set("从中间列表点选一条存档")
            self.refresh_versions_ui()
            return
        # Primary detail follows the active (last) selection in multi-select.
        index = selection[-1]
        if index >= len(self._saves_index):
            return
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
        ttk.Button(btn_row, text="浏览…", command=browse).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="取消", command=cancel).pack(side=tk.RIGHT)
        ttk.Button(btn_row, text="保存", style="Accent.TButton", command=save).pack(side=tk.RIGHT, padx=(0, 8))

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
        self.save_list.delete(0, tk.END)
        self._saves_index = []
        previous = self._selected_save
        self._selected_save = None
        for _group_name, saves in self.state.grouped_saves():
            for save in saves:
                star = "★  " if self.state.is_starred(save) else ""
                title = save.title_id or ""
                extra = f"    {title}" if title else ""
                status = self.state.save_status(save)
                tag = BACKUP_STATUS_LABELS.get(status.status, status.status)
                self.save_list.insert(tk.END, f"[{tag}] {star}{save.display_name}{extra}")
                self._saves_index.append(save)
                if previous and previous.path == save.path:
                    self._selected_save = save
                    self.save_list.selection_set(len(self._saves_index) - 1)
        if self._selected_save:
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
        """Cancel the poll loop and stop the volume watcher, leaving the window alone.

        Split out from ``on_close`` so tests can tear down an app while reusing a
        single shared Tk root (destroying and recreating Tk in one process is not
        reliable on macOS and hangs the event loop).
        """
        if getattr(self, "_poll_job", None):
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if getattr(self, "_initial_select_job", None):
            try:
                self.root.after_cancel(self._initial_select_job)
            except Exception:
                pass
            self._initial_select_job = None
        self.state.stop_watch(timeout=0.5)

    def on_close(self) -> None:
        self._stop_background()
        self.root.destroy()


def build_app(state: Optional[AppState] = None, root: Optional[tk.Tk] = None) -> VajSaveApp:
    return VajSaveApp(root=root, state=state)
