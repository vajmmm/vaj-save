import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Optional, Union

from .app_state import AppState
from .models import SaveEntry, VolumeInfo


def open_in_file_manager(path: Union[Path, str]) -> tuple[bool, str]:
    """Reveal a path in the OS file manager (Finder / Explorer / File Manager)."""
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
        return False, f"打开文件管理器失败: {e}"


class VajSaveApp:
    """Tkinter / ttk GUI wrapper for vaj-save desktop application."""

    def __init__(self, root: Optional[tk.Tk] = None, state: Optional[AppState] = None) -> None:
        self.root = root or tk.Tk()
        self.state = state or AppState()

        self._selected_save: Optional[SaveEntry] = None
        self._watch_var = tk.BooleanVar(value=True)

        self._init_window()
        self._create_widgets()
        self._bind_events()

        # Initial data load
        self.refresh_volumes_ui()
        if self._watch_var.get():
            self.state.start_watch()

        # Start periodic polling for background watch events
        self._poll_interval_ms = 200
        self._poll_job = self.root.after(self._poll_interval_ms, self._poll_events)

    def _init_window(self) -> None:
        self.root.title("vaj-save")
        self.root.minsize(900, 560)
        self.root.geometry("960x600")

    def _create_widgets(self) -> None:
        # Top-level paned layout
        self.main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))

        # -------------------------------------------------------------
        # Left Pane: 设备 / 卷 (Devices / Volumes)
        # -------------------------------------------------------------
        self.left_frame = ttk.LabelFrame(self.main_paned, text="设备 / 卷", padding=6)
        self.main_paned.add(self.left_frame, weight=1)

        # Volume treeview
        vol_tree_frame = ttk.Frame(self.left_frame)
        vol_tree_frame.pack(fill=tk.BOTH, expand=True)

        self.vol_tree = ttk.Treeview(
            vol_tree_frame,
            columns=("name", "path"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.vol_tree.heading("name", text="卷名称")
        self.vol_tree.heading("path", text="挂载路径")
        self.vol_tree.column("name", width=110, stretch=False)
        self.vol_tree.column("path", width=170, stretch=True)

        vol_vscroll = ttk.Scrollbar(vol_tree_frame, orient=tk.VERTICAL, command=self.vol_tree.yview)
        vol_hscroll = ttk.Scrollbar(vol_tree_frame, orient=tk.HORIZONTAL, command=self.vol_tree.xview)
        self.vol_tree.configure(yscrollcommand=vol_vscroll.set, xscrollcommand=vol_hscroll.set)

        self.vol_tree.grid(row=0, column=0, sticky="nsew")
        vol_vscroll.grid(row=0, column=1, sticky="ns")
        vol_hscroll.grid(row=1, column=0, sticky="ew")
        vol_tree_frame.grid_rowconfigure(0, weight=1)
        vol_tree_frame.grid_columnconfigure(0, weight=1)

        # Volume buttons and toggles
        btn_frame = ttk.Frame(self.left_frame, padding=(0, 6, 0, 0))
        btn_frame.pack(fill=tk.X)

        self.refresh_btn = ttk.Button(btn_frame, text="刷新", command=self.on_refresh_clicked)
        self.refresh_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.open_folder_btn = ttk.Button(btn_frame, text="打开文件夹…", command=self.on_open_folder_clicked)
        self.open_folder_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.watch_check = ttk.Checkbutton(
            btn_frame,
            text="监听插拔",
            variable=self._watch_var,
            command=self.on_watch_toggle,
        )
        self.watch_check.pack(side=tk.RIGHT)

        # -------------------------------------------------------------
        # Right Pane: 存档列表 (Saves Table)
        # -------------------------------------------------------------
        self.right_frame = ttk.LabelFrame(self.main_paned, text="存档列表", padding=6)
        self.main_paned.add(self.right_frame, weight=3)

        save_tree_frame = ttk.Frame(self.right_frame)
        save_tree_frame.pack(fill=tk.BOTH, expand=True)

        self.save_tree = ttk.Treeview(
            save_tree_frame,
            columns=("platform", "source", "name", "title_id", "slot", "user", "path"),
            show="headings",
            selectmode="browse",
        )
        self.save_tree.heading("platform", text="平台")
        self.save_tree.heading("source", text="来源")
        self.save_tree.heading("name", text="名称")
        self.save_tree.heading("title_id", text="Title ID")
        self.save_tree.heading("slot", text="槽位")
        self.save_tree.heading("user", text="用户")
        self.save_tree.heading("path", text="路径")

        self.save_tree.column("platform", width=70, stretch=False)
        self.save_tree.column("source", width=110, stretch=False)
        self.save_tree.column("name", width=180, stretch=True)
        self.save_tree.column("title_id", width=95, stretch=False)
        self.save_tree.column("slot", width=75, stretch=False)
        self.save_tree.column("user", width=75, stretch=False)
        self.save_tree.column("path", width=220, stretch=True)

        save_vscroll = ttk.Scrollbar(save_tree_frame, orient=tk.VERTICAL, command=self.save_tree.yview)
        save_hscroll = ttk.Scrollbar(save_tree_frame, orient=tk.HORIZONTAL, command=self.save_tree.xview)
        self.save_tree.configure(yscrollcommand=save_vscroll.set, xscrollcommand=save_hscroll.set)

        self.save_tree.grid(row=0, column=0, sticky="nsew")
        save_vscroll.grid(row=0, column=1, sticky="ns")
        save_hscroll.grid(row=1, column=0, sticky="ew")
        save_tree_frame.grid_rowconfigure(0, weight=1)
        save_tree_frame.grid_columnconfigure(0, weight=1)

        # Detail and action bar
        detail_frame = ttk.Frame(self.right_frame, padding=(0, 6, 0, 0))
        detail_frame.pack(fill=tk.X)

        ttk.Label(detail_frame, text="路径:").pack(side=tk.LEFT, padx=(0, 4))
        self.path_entry_var = tk.StringVar(value="")
        self.path_entry = ttk.Entry(detail_frame, textvariable=self.path_entry_var, state="readonly")
        self.path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        finder_btn_text = "在访达中显示" if sys.platform == "darwin" else "在文件管理器中显示"
        self.show_in_finder_btn = ttk.Button(
            detail_frame,
            text=finder_btn_text,
            command=self.on_show_in_finder_clicked,
        )
        self.show_in_finder_btn.pack(side=tk.RIGHT, padx=(4, 0))

        self.copy_path_btn = ttk.Button(
            detail_frame,
            text="复制路径",
            command=self.on_copy_path_clicked,
        )
        self.copy_path_btn.pack(side=tk.RIGHT)

        # -------------------------------------------------------------
        # Bottom Status Bar
        # -------------------------------------------------------------
        self.status_frame = ttk.Frame(self.root, padding=(8, 4, 8, 6))
        self.status_frame.pack(fill=tk.X, side=tk.BOTTOM)

        self.status_label_var = tk.StringVar(value="就绪")
        self.status_label = ttk.Label(
            self.status_frame,
            textvariable=self.status_label_var,
            anchor=tk.W,
        )
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.warning_label_var = tk.StringVar(value="")
        self.warning_label = ttk.Label(
            self.status_frame,
            textvariable=self.warning_label_var,
            foreground="red",
            anchor=tk.E,
        )
        self.warning_label.pack(side=tk.RIGHT)

    def _bind_events(self) -> None:
        self.vol_tree.bind("<<TreeviewSelect>>", self.on_volume_selected)
        self.save_tree.bind("<<TreeviewSelect>>", self.on_save_selected)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_refresh_clicked(self) -> None:
        self.state.refresh_volumes()
        self.refresh_volumes_ui()

    def on_open_folder_clicked(self) -> None:
        chosen_dir = filedialog.askdirectory(title="选择扫描目录")
        if chosen_dir:
            self.state.select_custom_path(chosen_dir)
            self.refresh_volumes_ui(select_path=Path(chosen_dir))
            self.refresh_saves_ui()

    def on_watch_toggle(self) -> None:
        if self._watch_var.get():
            self.state.start_watch()
            self.update_status("已开启插拔监听")
        else:
            self.state.stop_watch()
            self.update_status("已停止插拔监听")

    def on_volume_selected(self, event=None) -> None:
        selected_items = self.vol_tree.selection()
        if not selected_items:
            return
        item_id = selected_items[0]
        # Get mount point from treeview
        values = self.vol_tree.item(item_id, "values")
        if values and len(values) >= 2:
            mount_path = Path(values[1])
            self.state.select_mount(mount_path)
            self.refresh_saves_ui()

    def on_save_selected(self, event=None) -> None:
        selected_items = self.save_tree.selection()
        if not selected_items or not self.state.current_result:
            self._selected_save = None
            self.path_entry_var.set("")
            return
        item_id = selected_items[0]
        # Match save entry by index tag or path value
        values = self.save_tree.item(item_id, "values")
        if values and len(values) >= 7:
            save_path = values[6]
            for s in self.state.current_result.saves:
                if s.path == save_path:
                    self._selected_save = s
                    self.path_entry_var.set(s.path)
                    break

    def on_show_in_finder_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("未选中任何存档")
            return
        ok, msg = open_in_file_manager(self._selected_save.path)
        if ok:
            self.update_status(msg)
            self.update_warning("")
        else:
            self.update_warning(msg)

    def on_copy_path_clicked(self) -> None:
        if not self._selected_save:
            self.update_warning("未选中任何存档")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self._selected_save.path)
        self.update_status(f"已复制路径到剪贴板: {self._selected_save.path}")

    def _poll_events(self) -> None:
        """Periodic timer callback to drain events from AppState queue."""
        drained = self.state.drain_events()
        if drained > 0:
            self.refresh_volumes_ui()
            self.refresh_saves_ui()
        self._poll_job = self.root.after(self._poll_interval_ms, self._poll_events)

    def refresh_volumes_ui(self, select_path: Optional[Path] = None) -> None:
        """Re-populate the volume list in the UI."""
        self.vol_tree.delete(*self.vol_tree.get_children())
        target_path = select_path or self.state.current_mount

        selected_item_id = None
        for vol in self.state.volumes:
            item_id = self.vol_tree.insert(
                "",
                tk.END,
                values=(vol.name, str(vol.mount_point)),
            )
            if target_path and Path(vol.mount_point) == Path(target_path):
                selected_item_id = item_id

        if selected_item_id:
            self.vol_tree.selection_set(selected_item_id)
            self.vol_tree.focus(selected_item_id)

        self.update_status(self.state.status_text)
        if self.state.warnings:
            self.update_warning("; ".join(self.state.warnings))
        else:
            self.update_warning("")

    def refresh_saves_ui(self) -> None:
        """Re-populate the saves table based on current AppState."""
        self.save_tree.delete(*self.save_tree.get_children())
        self._selected_save = None
        self.path_entry_var.set("")

        if self.state.current_result:
            for save in self.state.current_result.saves:
                self.save_tree.insert(
                    "",
                    tk.END,
                    values=(
                        save.platform.upper(),
                        save.source_id,
                        save.display_name,
                        save.title_id or "-",
                        save.slot or "-",
                        save.user or "-",
                        save.path,
                    ),
                )

        self.update_status(self.state.status_text)
        if self.state.warnings:
            self.update_warning("; ".join(self.state.warnings))
        else:
            self.update_warning("")

    def update_status(self, text: str) -> None:
        self.status_label_var.set(text)

    def update_warning(self, text: str) -> None:
        self.warning_label_var.set(text)

    def on_close(self) -> None:
        """Clean up background watch thread and close window."""
        if hasattr(self, "_poll_job") and self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
        self.state.stop_watch(timeout=0.5)
        self.root.destroy()


def build_app(state: Optional[AppState] = None, root: Optional[tk.Tk] = None) -> VajSaveApp:
    """Factory function to instantiate the Tkinter desktop app."""
    return VajSaveApp(root=root, state=state)
