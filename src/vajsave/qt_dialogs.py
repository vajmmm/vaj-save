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
from .ftp_session import parse_port
from .library import load_keep_last
from .library_import import MANIFEST_NAME
from .models import SaveEntry, VolumeInfo
from .remote_ftp import FtpProfile, RemoteFtpClient, get_preset
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
    "同一张卡用文件系统卷序列号记住存档根目录，换转接头仍能认出；刷新设备会全量扫描。\n"
    "Dock 含 GB / GBC 独立机种。恢复只拷到你选的文件夹，不会写入掌机。"
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
    add_device_requested = Signal()
    choose_device_requested = Signal()
    ftp_requested = Signal()

    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        form = QFormLayout(self)
        library_row, self.library = _dir_row(self, str(state.library_root), "libraryDirectory")

        device_row = QHBoxLayout()
        device_row.setSpacing(8)
        self.add_device_btn = _button("添加设备…", "fa6s.plus")
        self.add_device_btn.setObjectName("addDeviceBtn")
        self.add_device_btn.clicked.connect(self.add_device_requested.emit)

        self.choose_device_btn = _button("其他设备…", "fa6s.display")
        self.choose_device_btn.setObjectName("chooseDeviceBtn")
        self.choose_device_btn.clicked.connect(self.choose_device_requested.emit)

        self.ftp_btn = _button("FTP 拉取…", "fa6s.cloud-arrow-down")
        self.ftp_btn.setObjectName("ftpBtn")
        self.ftp_btn.clicked.connect(self.ftp_requested.emit)

        device_row.addWidget(self.add_device_btn)
        device_row.addWidget(self.choose_device_btn)
        device_row.addWidget(self.ftp_btn)
        device_row.addStretch(1)

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
        form.addRow("掌机/外部设备", device_row)
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


PRESET_HINTS = {
    "vita": "提示：在 PS Vita 的 VitaShell 中按 SELECT 键启动 FTP（默认端口 1337）",
    "switch": "提示：在 Switch 上运行 sys-ftpd-light 后台模块或 JKSV/ftpd（默认端口 5000）",
    "3ds": "提示：在 3DS 上启动 FTPD 应用，屏幕将显示当前 IP（默认端口 5000）",
    "psp": "提示：在 PSP 上运行 PSP-FTPD 并连接 Wi-Fi 热点（默认端口 21）",
    "nds": "提示：在烧录卡上运行 ftpd-nds 并连接 Wi-Fi 热点（默认端口 21）",
    "ps3": "提示：在 PS3 上安装并开启 webMAN MOD 后台 FTP（默认端口 21）",
    "ps4": "提示：在 PS4 上开启 GoldHEN 的 FTP 服务（默认端口 2121）",
    "wiiu": "提示：在 Wii U 上运行 FTPiiU Everywhere 或 Aroma FTP 插件（默认端口 21）",
    "wii": "提示：在 Wii 的 Homebrew Channel 中运行 ftpii 应用（默认端口 21）",
    "x360": "提示：在 Xbox 360 上运行 Aurora 极光桌面或 DashLaunch FTP 服务（默认端口 21）",
    "checkpoint": "提示：专用于 Checkpoint 导出的存档网络服务器（默认端口 5000）",
    "ftpd": "提示：通用 FTP 协议，适用于各种自制设备与服务（默认端口 21）",
}


def _clean_host_and_port(raw_host: str, raw_port: str) -> tuple[str, str]:
    text = str(raw_host or "").strip()
    port = str(raw_port or "").strip()
    if text.startswith("ftp://"):
        text = text[6:]
    elif text.startswith("http://"):
        text = text[7:]
    text = text.rstrip("/")
    if ":" in text:
        parts = text.split(":", 1)
        text = parts[0]
        if parts[1].isdigit():
            port = parts[1]
    return text, port


class FtpDialog(QDialog):
    def __init__(self, state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("连接无线设备 / FTP 同步")
        self.setMinimumWidth(400)
        form = QFormLayout(self)
        form.setSpacing(8)

        self.preset = QComboBox()
        for profile in state.ftp_presets():
            self.preset.addItem(profile.label, profile.key)
        self.preset.setCurrentIndex(max(0, self.preset.findData(state.ftp_preset_key)))

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {SWITCH['muted_strong']}; font-size: 11px;")

        self.host = QLineEdit(state.ftp_host or "192.168.")
        self.host.setPlaceholderText("例如: 192.168.1.100 或直接粘贴屏幕地址")
        self.port = QLineEdit(str(state.ftp_port or ""))
        self.user = QLineEdit(state.ftp_user or "anonymous")
        self.password = QLineEdit(state._ftp_password if state.ftp_remember_password else "")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember = QCheckBox("记住密码")
        self.remember.setObjectName("rememberFtpPassword")
        self.remember.setChecked(state.ftp_remember_password)

        current_key = self.preset.currentData()
        current_profile = get_preset(current_key)
        if not self.port.text().strip():
            self.port.setText(str(current_profile.port or ""))
        self._last_default_port = str(current_profile.port or "")
        self.hint.setText(PRESET_HINTS.get(current_profile.key, current_profile.description))

        self.preset.currentIndexChanged.connect(self._on_preset_changed)

        form.addRow("设备平台", self.preset)
        form.addRow("", self.hint)
        form.addRow("主机 IP", self.host)
        form.addRow("端口", self.port)
        form.addRow("用户名", self.user)
        form.addRow("密码", self.password)
        form.addRow("", self.remember)

        test_row = QHBoxLayout()
        self.btn_test = QPushButton("测试连接")
        self.btn_test.setFixedWidth(80)
        self.btn_test.clicked.connect(self._test_connection)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        self.test_status.setStyleSheet("font-size: 11px;")
        test_row.addWidget(self.btn_test)
        test_row.addWidget(self.test_status, 1)
        form.addRow("", test_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("连接并同步")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _test_connection(self) -> None:
        clean_host, clean_port = _clean_host_and_port(self.host.text(), self.port.text())
        self.host.setText(clean_host)
        if clean_port:
            self.port.setText(clean_port)
        parsed_port = parse_port(clean_port) or 21
        self.test_status.setText("正在测试连接…")
        self.test_status.setStyleSheet(f"color: {SWITCH['muted_strong']}; font-size: 11px;")

        factory = getattr(self.state, "_ftp_client_factory", None) or (
            lambda p: RemoteFtpClient(p, timeout=5.0)
        )
        profile = FtpProfile(
            key="test",
            label="test",
            host=clean_host,
            port=parsed_port,
            user=self.user.text() or "anonymous",
            password=self.password.text(),
        )
        try:
            with factory(profile) as client:
                pass
            self.test_status.setText("● 连接成功！掌机在线")
            self.test_status.setStyleSheet(f"color: {SWITCH['status_green']}; font-size: 11px; font-weight: bold;")
        except Exception as exc:
            self.test_status.setText(f"● 无法连接: {exc}")
            self.test_status.setStyleSheet("color: #dc3545; font-size: 11px;")

    def _on_preset_changed(self) -> None:
        key = self.preset.currentData()
        profile = get_preset(key)
        current_port = self.port.text().strip()
        if not current_port or current_port == self._last_default_port:
            self.port.setText(str(profile.port or ""))
        self._last_default_port = str(profile.port or "")
        self.hint.setText(PRESET_HINTS.get(profile.key, profile.description))

    def configure(self) -> None:
        clean_host, clean_port = _clean_host_and_port(self.host.text(), self.port.text())
        self.state.configure_ftp(
            clean_host,
            clean_port,
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
