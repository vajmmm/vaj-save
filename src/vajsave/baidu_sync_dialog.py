"""百度网盘应用配置与设备码授权对话框。"""

from __future__ import annotations

import time
from typing import Optional

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .artwork import ArtworkLoader
from .baidu_api import (
    BaiduCredentialStore,
    BaiduNetdiskClient,
    BaiduSyncError,
    DeviceAuthorization,
)


class BaiduSyncDialog(QDialog):
    """保存用户自有百度开放平台应用信息并完成设备码授权。"""

    def __init__(
        self,
        loader: ArtworkLoader,
        credential_store: Optional[BaiduCredentialStore] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.loader = loader
        self.credentials = credential_store or BaiduCredentialStore()
        self.client = BaiduNetdiskClient(self.credentials)
        self._closed = False
        self._device_code = ""
        self._verification_url = ""
        self._expires_at = 0.0
        self._poll_interval = 5

        self.setWindowTitle("百度网盘同步")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.app_key = QLineEdit()
        self.app_secret = QLineEdit()
        self.app_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.app_name = QLineEdit("vaj-save")
        form.addRow("App Key", self.app_key)
        form.addRow("Secret Key", self.app_secret)
        form.addRow("应用名称", self.app_name)
        layout.addLayout(form)

        self.description = QLabel(
            "请使用自己在百度网盘开放平台创建、已启用文件访问和设备码授权的应用。"
            "应用密钥和授权令牌只保存在系统凭据库中。"
        )
        self.description.setWordWrap(True)
        layout.addWidget(self.description)

        self.auth_status = QLabel("")
        self.auth_status.setWordWrap(True)
        layout.addWidget(self.auth_status)
        self.user_code = QLabel("")
        self.user_code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.user_code.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(self.user_code)
        self.verification_url_label = QLabel("")
        self.verification_url_label.setWordWrap(True)
        self.verification_url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.verification_url_label.hide()
        layout.addWidget(self.verification_url_label)

        controls = QHBoxLayout()
        self.connect_button = QPushButton("保存并连接")
        self.open_browser_button = QPushButton("打开百度授权页")
        self.open_browser_button.setEnabled(False)
        self.disconnect_button = QPushButton("断开并清除授权")
        controls.addWidget(self.connect_button)
        controls.addWidget(self.open_browser_button)
        controls.addWidget(self.disconnect_button)
        layout.addLayout(controls)

        self.close_button = QPushButton("关闭")
        layout.addWidget(self.close_button)
        self.poll_timer = QTimer(self)
        self.poll_timer.setSingleShot(True)
        self.poll_timer.timeout.connect(self._poll_device_token)
        self.connect_button.clicked.connect(self._connect)
        self.open_browser_button.clicked.connect(self._open_authorization_page)
        self.disconnect_button.clicked.connect(self._disconnect)
        self.close_button.clicked.connect(self.reject)

        try:
            saved = self.credentials.load()
        except BaiduSyncError as exc:
            self.auth_status.setText(str(exc))
            self.disconnect_button.setEnabled(False)
        else:
            if saved is not None:
                self.app_key.setText(saved.app_key)
                self.app_secret.setText(saved.app_secret)
                self.app_name.setText(saved.app_name)
                self.auth_status.setText(
                    "已连接百度网盘。"
                    if saved.connected
                    else "已保存应用信息，尚未完成百度网盘授权。"
                )
                self.disconnect_button.setEnabled(True)
            else:
                self.disconnect_button.setEnabled(False)

    def _connect(self) -> None:
        self.poll_timer.stop()
        self._device_code = ""
        self._verification_url = ""
        self.user_code.clear()
        self.verification_url_label.clear()
        self.verification_url_label.hide()
        self.open_browser_button.setEnabled(False)
        app_key = self.app_key.text().strip()
        try:
            self.credentials.save_app(
                self.app_key.text(), self.app_secret.text(), self.app_name.text()
            )
        except BaiduSyncError as exc:
            self.auth_status.setText(str(exc))
            return
        self.connect_button.setEnabled(False)
        self.auth_status.setText("正在向百度申请设备授权码…")

        def task():
            try:
                return True, self.client.request_device_code(app_key)
            except BaiduSyncError as exc:
                return False, str(exc)
            except Exception as exc:  # noqa: BLE001 - avoid exposing a request URL
                return False, f"百度设备授权请求异常：{type(exc).__name__}。"

        def completed(result) -> None:
            if self._closed:
                return
            self.connect_button.setEnabled(True)
            if not result:
                self.auth_status.setText("授权请求未完成。")
                return
            ok, value = result
            if not ok:
                self.auth_status.setText(str(value))
                return
            authorization: DeviceAuthorization = value
            self._device_code = authorization.device_code
            self._expires_at = time.monotonic() + authorization.expires_in
            self._poll_interval = authorization.interval
            self.user_code.setText(f"授权码：{authorization.user_code}")
            self._verification_url = authorization.verification_url
            self.verification_url_label.setText(f"授权网址：{self._verification_url}")
            self.verification_url_label.show()
            self.auth_status.setText(
                "请打开百度授权页并输入上方授权码。此窗口会自动检查授权状态。"
            )
            self.open_browser_button.setEnabled(True)
            self._schedule_poll()

        self.loader.submit(("baidu-device-code", id(self)), task, completed)

    def _open_authorization_page(self) -> None:
        if self._verification_url:
            QDesktopServices.openUrl(QUrl(self._verification_url))

    def _schedule_poll(self) -> None:
        if self._closed or not self._device_code:
            return
        remaining = self._expires_at - time.monotonic()
        if remaining <= 0:
            self._device_code = ""
            self.open_browser_button.setEnabled(False)
            self.auth_status.setText("授权码已过期，请重新连接。")
            return
        self.poll_timer.start(max(1, min(self._poll_interval, int(remaining))) * 1000)

    def _poll_device_token(self) -> None:
        device_code = self._device_code
        if not device_code or self._closed:
            return
        self.auth_status.setText("正在检查百度授权状态…")

        def task():
            try:
                return True, self.client.poll_device_token(device_code)
            except BaiduSyncError as exc:
                return False, str(exc)
            except Exception as exc:  # noqa: BLE001 - avoid exposing a request URL
                return False, f"百度授权状态检查异常：{type(exc).__name__}。"

        def completed(result) -> None:
            if self._closed or self._device_code != device_code:
                return
            if not result:
                self.auth_status.setText("无法检查授权状态，请重试。")
                self._device_code = ""
                self.open_browser_button.setEnabled(False)
                return
            ok, value = result
            if not ok:
                self.auth_status.setText(str(value))
                self._device_code = ""
                self.open_browser_button.setEnabled(False)
                return
            if value.status == "authorized":
                self._device_code = ""
                self.open_browser_button.setEnabled(False)
                self.auth_status.setText("百度网盘已连接，可以开始同步。")
                return
            if value.interval_increased:
                self._poll_interval += 5
            self.auth_status.setText("等待你在浏览器中完成授权…")
            self._schedule_poll()

        self.loader.submit(("baidu-device-poll", id(self)), task, completed)

    def _disconnect(self) -> None:
        self.poll_timer.stop()
        self._device_code = ""
        self._verification_url = ""
        try:
            self.credentials.clear()
        except BaiduSyncError as exc:
            QMessageBox.warning(self, "断开百度网盘", str(exc))
            return
        self.app_key.clear()
        self.app_secret.clear()
        self.app_name.setText("vaj-save")
        self.user_code.clear()
        self.verification_url_label.clear()
        self.verification_url_label.hide()
        self.auth_status.setText("已清除系统凭据库中的百度网盘信息。")
        self.open_browser_button.setEnabled(False)
        self.disconnect_button.setEnabled(False)

    def reject(self) -> None:
        self._closed = True
        self.poll_timer.stop()
        super().reject()

    def accept(self) -> None:
        self._closed = True
        self.poll_timer.stop()
        super().accept()


__all__ = ["BaiduSyncDialog"]
