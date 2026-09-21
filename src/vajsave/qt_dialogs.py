"""Settings, FTP, LLM, help, device picker, and ZIP-import dialogs."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

import qtawesome as qta
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .app_state import PLATFORM_LABELS, PLATFORM_ORDER, AppState
from .artwork import (
    LLM_PROTOCOLS,
    ArtworkLoader,
    default_base_url,
    default_model,
)
from .library import load_keep_last
from .library_import import MANIFEST_NAME
from .models import SaveEntry, VolumeInfo
from .ui_theme import SWITCH


def _icon(name: str, color: str = SWITCH["ink"], scale: float = 1.0):
    return qta.icon(name, color=color, scale_factor=scale)


def _button(
    text: str,
    icon_name: Optional[str] = None,
    *,
    primary: bool = False,
    checkable: bool = False,
) -> QPushButton:
    button = QPushButton(text)
    if icon_name:
        button.setIcon(_icon(name=icon_name, color=SWITCH["on_accent"] if primary else SWITCH["ink"], scale=0.82))
        button.setIconSize(QSize(18, 18))
    button.setProperty("primary", primary)
    button.setCheckable(checkable)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def zip_has_manifest(zip_path: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path) as archive:
            return MANIFEST_NAME in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def _dir_row(parent: QWidget, initial: str, object_name: str = "") -> tuple[QWidget, QLineEdit]:
    row = QWidget(parent)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    field = QLineEdit(initial)
    if object_name:
        field.setObjectName(object_name)
    browse = QPushButton("浏览…")
    browse.setObjectName("browseButton")
    browse.setCursor(Qt.CursorShape.PointingHandCursor)

    def pick() -> None:
        chosen = QFileDialog.getExistingDirectory(parent, "选择目录", field.text())
        if chosen:
            field.setText(chosen)

    browse.clicked.connect(pick)
    layout.addWidget(field, 1)
    layout.addWidget(browse)
    return row, field


HELP_TEXT = (
    "vaj-save 将掌机存档只读备份到电脑，不会写入掌机。\n\n"
    "选择游戏后可备份、恢复到指定文件夹、导出 ZIP、绑定 ROM 或记录备注。\n"
    "详情抽屉可收藏游戏、删除单个版本（删掉最后一版等于删除整条游戏）。\n"
    "本地库可导入带清单的 ZIP；旧 ZIP 需挂到已有游戏或填写机种与名称新建。\n"
    "「备份有更新」只备份新的或有变化的存档，过程中可取消。\n"
    "插入后自动备份默认关闭，可在设置中打开。\n"
    "FTP 拉取只读且增量跳过未改文件；可选记住密码。\n"
    "Dock 含 GB / GBC 独立机种。"
)


def show_help(parent: Optional[QWidget] = None) -> None:
    QMessageBox.information(parent, "帮助", HELP_TEXT)


class LLMSettingsDialog(QDialog):
    """可选的 LLM 封面消歧设置，完整复用 AppState 的持久化能力。"""

    def __init__(self, state: AppState, loader: ArtworkLoader, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.loader = loader
        self.setWindowTitle("LLM 封面消歧")
        self.setMinimumWidth(520)
        form = QFormLayout(self)
        self.enabled = QCheckBox("启用（仅在封面候选存在歧义时使用）")
        self.enabled.setChecked(state.llm_cover_enabled)
        self.protocol = QComboBox()
        labels = {
            "openai-completions": "OpenAI 兼容 Chat Completions",
            "anthropic-messages": "Anthropic Messages",
        }
        for value in LLM_PROTOCOLS:
            self.protocol.addItem(labels.get(value, value), value)
        self.protocol.setCurrentIndex(max(0, self.protocol.findData(state.llm_protocol)))
        self.base_url = QLineEdit(state.llm_base_url)
        self.api_key = QLineEdit(state.llm_api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = QLineEdit(state.llm_model)
        form.addRow("", self.enabled)
        form.addRow("协议", self.protocol)
        form.addRow("Base URL", self.base_url)
        form.addRow("API 密钥", self.api_key)
        form.addRow("模型 ID", self.model)
        test_row = QHBoxLayout()
        self.test_button = _button("测试连接", "fa6s.plug-circle-check")
        self.test_result = QLabel("")
        self.test_result.setObjectName("sectionMuted")
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        form.addRow("", test_row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._last_protocol = self.protocol.currentData()
        self.protocol.currentIndexChanged.connect(self._protocol_changed)
        self.test_button.clicked.connect(self._test_connection)

    def _protocol_changed(self) -> None:
        new_protocol = self.protocol.currentData()
        old_protocol = self._last_protocol
        if self.base_url.text().strip().rstrip("/") == default_base_url(old_protocol).rstrip("/"):
            self.base_url.setText(default_base_url(new_protocol))
        if self.model.text().strip() == default_model(old_protocol):
            self.model.setText(default_model(new_protocol))
        self._last_protocol = new_protocol

    def _values(self) -> dict[str, object]:
        return {
            "enabled": self.enabled.isChecked(),
            "protocol": self.protocol.currentData(),
            "base_url": self.base_url.text().strip(),
            "api_key": self.api_key.text().strip(),
            "model": self.model.text().strip(),
        }

    def _save(self) -> None:
        self.state.set_llm_cover(**self._values())
        self.accept()

    def _test_connection(self) -> None:
        values = self._values()
        self.test_button.setEnabled(False)
        self.test_result.setText("正在测试…")

        def task():
            return self.state.test_llm_cover(
                protocol=str(values["protocol"]),
                base_url=str(values["base_url"]),
                api_key=str(values["api_key"]),
                model=str(values["model"]),
            )

        def completed(result) -> None:
            self.test_button.setEnabled(True)
            ok, detail = result if result is not None else (False, "测试失败")
            self.test_result.setText(("✓ " if ok else "✗ ") + detail)

        self.loader.submit(("llm-probe", id(self)), task, completed)


class SettingsDialog(QDialog):
    llm_requested = Signal()

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        library_row, self.library = _dir_row(self, str(state.library_root), "libraryDirectory")
        gba_row, self.gba = _dir_row(self, str(state.gba_rom_dir or ""), "gbaRomDirectory")
        nds_row, self.nds = _dir_row(self, str(state.nds_rom_dir or ""), "ndsRomDirectory")
        gb_row, self.gb = _dir_row(self, str(state.gb_rom_dir or ""), "gbRomDirectory")
        gbc_row, self.gbc = _dir_row(self, str(state.gbc_rom_dir or ""), "gbcRomDirectory")
        libretro_row, self.libretro = _dir_row(
            self, str(state.libretro_dir or ""), "libretroDirectory"
        )
        self.keep = QLineEdit(str(load_keep_last(state.library_root)))
        self.auto_backup = QCheckBox("插入后自动备份有更新的存档")
        self.auto_backup.setObjectName("autoBackupOnInsert")
        self.auto_backup.setChecked(state.auto_backup_on_insert)
        form.addRow("本地备份库", library_row)
        form.addRow("GBA ROM 目录", gba_row)
        form.addRow("NDS ROM 目录", nds_row)
        form.addRow("GB ROM 目录", gb_row)
        form.addRow("GBC ROM 目录", gbc_row)
        form.addRow("Libretro 元数据目录", libretro_row)
        form.addRow("保留版本数（0 为不限）", self.keep)
        form.addRow("", self.auto_backup)
        self.llm_entry = _button(
            "配置…（已启用）" if state.llm_cover_enabled else "配置…（未启用）",
            "fa6s.wand-magic-sparkles",
        )
        self.llm_entry.setObjectName("llmSettingsEntry")
        self.llm_entry.clicked.connect(self.llm_requested.emit)
        form.addRow("LLM 封面消歧", self.llm_entry)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def refresh_llm_label(self) -> None:
        enabled = self.state.llm_cover_enabled
        self.llm_entry.setText("配置…（已启用）" if enabled else "配置…（未启用）")

    def apply(self) -> bool:
        state = self.state
        state.set_library_root(self.library.text())
        state.set_rom_dirs(
            self.gba.text(),
            self.nds.text(),
            gb_rom_dir=self.gb.text(),
            gbc_rom_dir=self.gbc.text(),
        )
        state.set_libretro_dir(self.libretro.text())
        state.set_auto_backup_on_insert(self.auto_backup.isChecked())
        return state.set_keep_last(self.keep.text()) is not None


class FtpDialog(QDialog):
    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("FTP 只读拉取")
        form = QFormLayout(self)
        self.preset = QComboBox()
        for profile in state.ftp_presets():
            self.preset.addItem(profile.label, profile.key)
        self.preset.setCurrentIndex(max(0, self.preset.findData(state.ftp_preset_key)))
        self.host = QLineEdit(state.ftp_host)
        self.port = QLineEdit(str(state.ftp_port or ""))
        self.user = QLineEdit(state.ftp_user)
        self.password = QLineEdit(state._ftp_password if state.ftp_remember_password else "")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("记住密码")
        self.remember.setObjectName("rememberFtpPassword")
        self.remember.setChecked(state.ftp_remember_password)
        form.addRow("预设", self.preset)
        form.addRow("主机", self.host)
        form.addRow("端口", self.port)
        form.addRow("用户", self.user)
        form.addRow("密码", self.password)
        form.addRow("", self.remember)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def configure(self) -> None:
        self.state.configure_ftp(
            self.host.text(),
            self.port.text(),
            self.user.text(),
            self.password.text(),
            self.preset.currentData(),
            remember_password=self.remember.isChecked(),
        )


class DevicePickerDialog(QDialog):
    def __init__(self, volumes: list[VolumeInfo], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择设备")
        layout = QVBoxLayout(self)
        self.listing = QListWidget()
        for volume in volumes:
            item = QListWidgetItem(f"{volume.name}\n{volume.mount_point}")
            item.setData(Qt.ItemDataRole.UserRole, str(volume.mount_point))
            self.listing.addItem(item)
        layout.addWidget(self.listing)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_mount(self) -> Optional[str]:
        item = self.listing.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)


class ZipImportDialog(QDialog):
    """Ask how to attach a legacy ZIP that has no vaj-save.json manifest."""

    def __init__(
        self,
        state: AppState,
        selected: Optional[SaveEntry],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self._selected = selected
        self.setWindowTitle("导入无清单 ZIP")
        layout = QVBoxLayout(self)
        hint = QLabel("这个 ZIP 没有 vaj-save 清单。请选择挂到当前游戏，或填写机种与名称新建。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.attach = QRadioButton("挂到当前选中游戏")
        self.create = QRadioButton("作为新游戏导入")
        layout.addWidget(self.attach)
        layout.addWidget(self.create)
        form = QFormLayout()
        self.platform = QComboBox()
        for key in PLATFORM_ORDER:
            if key == "all":
                continue
            self.platform.addItem(PLATFORM_LABELS.get(key, key), key)
        self.display_name = QLineEdit()
        self.title_id = QLineEdit()
        form.addRow("机种", self.platform)
        form.addRow("名称", self.display_name)
        form.addRow("Title ID", self.title_id)
        layout.addLayout(form)
        if selected is None:
            self.attach.setEnabled(False)
            self.create.setChecked(True)
        else:
            self.attach.setText(f"挂到当前选中游戏（{selected.display_name}）")
            self.attach.setChecked(True)
            index = self.platform.findData(selected.platform)
            if index >= 0:
                self.platform.setCurrentIndex(index)
            self.display_name.setText(selected.display_name)
            self.title_id.setText(selected.title_id or "")
        self.attach.toggled.connect(self._sync_fields)
        self._sync_fields()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync_fields(self) -> None:
        creating = self.create.isChecked()
        self.platform.setEnabled(creating)
        self.display_name.setEnabled(creating)
        self.title_id.setEnabled(creating)

    def import_kwargs(self) -> dict[str, Optional[str]]:
        if self.attach.isChecked() and self._selected is not None:
            return {
                "attach_game_id": self.state._game_id(self._selected),
            }
        name = self.display_name.text().strip()
        platform = str(self.platform.currentData() or "").strip()
        return {
            "new_platform": platform or None,
            "new_display_name": name or None,
            "new_title_id": self.title_id.text().strip() or None,
        }
