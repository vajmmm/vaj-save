"""Real-time log viewer dialog for VajSave.

Allows real-time inspection of scanning and cover acquisition processes.
Styled to match the Switch Basic White palette tokens.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .log_store import LogRecordEntry, LogStore, get_log_store
from .ui_theme import FONT_FAMILY, SWITCH, mix


class _LogBridge(QObject):
    log_received = Signal(object)


class LogViewerDialog(QDialog):
    """Modeless log viewer dialog showing live scanning and cover acquisition logs."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        store: Optional[LogStore] = None,
    ) -> None:
        super().__init__(parent)
        self.store = store or get_log_store()
        self._current_category = "all"
        self._search_text = ""
        self._auto_scroll = True

        self.setWindowTitle("运行日志 - 扫描与封面获取")
        self.resize(820, 520)
        self.setMinimumSize(560, 360)

        self._bridge = _LogBridge()
        self._bridge.log_received.connect(self._on_log_received)
        self._unsubscribe = self.store.subscribe(self._bridge.log_received.emit)

        self._init_ui()
        self._apply_theme()
        self._reload_logs()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # Toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # Category buttons
        self.cat_group = QButtonGroup(self)
        self.cat_group.setExclusive(True)

        self.btn_all = QPushButton("全部")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)
        self.btn_scan = QPushButton("扫描过程")
        self.btn_scan.setCheckable(True)
        self.btn_cover = QPushButton("封面获取")
        self.btn_cover.setCheckable(True)

        for idx, btn in enumerate((self.btn_all, self.btn_scan, self.btn_cover)):
            btn.setObjectName("logCatBtn")
            self.cat_group.addButton(btn, idx)
            toolbar.addWidget(btn)

        self.cat_group.idClicked.connect(self._on_category_changed)

        # Search field
        self.search_input = QLineEdit()
        self.search_input.setObjectName("logSearchInput")
        self.search_input.setPlaceholderText("过滤关键字...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_input, 1)

        # Auto scroll
        self.auto_scroll_cb = QCheckBox("自动滚动")
        self.auto_scroll_cb.setObjectName("logAutoScroll")
        self.auto_scroll_cb.setChecked(True)
        self.auto_scroll_cb.toggled.connect(self._on_auto_scroll_toggled)
        toolbar.addWidget(self.auto_scroll_cb)

        # Actions
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("logClearBtn")
        self.btn_clear.clicked.connect(self._on_clear)
        toolbar.addWidget(self.btn_clear)

        self.btn_copy = QPushButton("复制全部")
        self.btn_copy.setObjectName("logCopyBtn")
        self.btn_copy.clicked.connect(self._on_copy)
        toolbar.addWidget(self.btn_copy)

        layout.addLayout(toolbar)

        # Log Text Box
        self.text_browser = QTextBrowser()
        self.text_browser.setReadOnly(True)
        self.text_browser.setOpenExternalLinks(False)
        self.text_browser.setObjectName("logTextArea")
        font = QFont("Consolas, SF Mono, Menlo, Courier New", 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_browser.setFont(font)
        layout.addWidget(self.text_browser, 1)

        # Status row
        status_row = QHBoxLayout()
        self.lbl_count = QLabel("共 0 条日志")
        self.lbl_count.setObjectName("logCountLabel")
        status_row.addWidget(self.lbl_count)
        status_row.addStretch()

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("logCloseBtn")
        self.btn_close.clicked.connect(self.close)
        status_row.addWidget(self.btn_close)

        layout.addLayout(status_row)

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {SWITCH['fog_canvas']};
                color: {SWITCH['ink']};
                font-family: '{FONT_FAMILY}';
            }}
            #logCatBtn {{
                background-color: {SWITCH['panel_alt']};
                color: {SWITCH['ink']};
                border: 1px solid {SWITCH['border_soft']};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 500;
            }}
            #logCatBtn:hover {{
                background-color: {SWITCH['card']};
                border-color: {mix(SWITCH['border_soft'], SWITCH['shadow_deep'], 0.3)};
            }}
            #logCatBtn:checked {{
                background-color: {SWITCH['selected_soft']};
                border-color: {SWITCH['accent']};
                color: {SWITCH['accent']};
                font-weight: 600;
            }}
            #logSearchInput {{
                background-color: #ffffff;
                color: {SWITCH['ink']};
                border: 1px solid {SWITCH['border_soft']};
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }}
            #logAutoScroll {{
                font-size: 12px;
                color: {SWITCH['muted_strong']};
            }}
            #logTextArea {{
                background-color: #ffffff;
                color: {SWITCH['ink']};
                border: 1px solid {SWITCH['border_soft']};
                border-radius: 8px;
                padding: 10px;
                line-height: 1.45;
            }}
            #logCountLabel {{
                font-size: 11px;
                color: {SWITCH['muted_strong']};
            }}
            #logClearBtn, #logCopyBtn, #logCloseBtn {{
                background-color: {SWITCH['panel_alt']};
                color: {SWITCH['ink']};
                border: 1px solid {SWITCH['border_soft']};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            #logClearBtn:hover, #logCopyBtn:hover, #logCloseBtn:hover {{
                background-color: {SWITCH['card']};
                border-color: {mix(SWITCH['border_soft'], SWITCH['shadow_deep'], 0.3)};
            }}
        """)

    def _format_entry_html(self, entry: LogRecordEntry) -> str:
        # Category badge
        if entry.category == "scan":
            cat_badge = '<span style="color:#0a84ff; font-weight:bold;">[扫描]</span>'
        elif entry.category == "cover":
            cat_badge = '<span style="color:#28a745; font-weight:bold;">[封面]</span>'
        else:
            cat_badge = '<span style="color:#718096;">[应用]</span>'

        # Level tag & color
        lvl = entry.level.upper()
        if lvl == "WARNING":
            lvl_span = '<span style="color:#d97706; font-weight:bold;">[WARN]</span>'
            msg_color = "#92400e"
        elif lvl == "ERROR":
            lvl_span = '<span style="color:#dc2626; font-weight:bold;">[ERROR]</span>'
            msg_color = "#b91c1c"
        elif lvl == "DEBUG":
            lvl_span = '<span style="color:#94a3b8;">[DEBUG]</span>'
            msg_color = "#64748b"
        else:
            lvl_span = '<span style="color:#475569;">[INFO]</span>'
            msg_color = "#1e293b"

        time_span = f'<span style="color:#94a3b8;">[{entry.formatted_time}]</span>'
        escaped_msg = (
            entry.message.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace(" ", "&nbsp;")
        )
        return (
            f'<div style="margin: 2px 0;">'
            f'{time_span} {cat_badge} {lvl_span} <span style="color:{msg_color};">{escaped_msg}</span>'
            f'</div>'
        )

    def _match_entry(self, entry: LogRecordEntry) -> bool:
        if self._current_category != "all" and entry.category != self._current_category:
            return False
        if self._search_text:
            text = self._search_text
            if text not in entry.message.casefold() and text not in entry.logger_name.casefold():
                return False
        return True

    def _on_log_received(self, entry: LogRecordEntry) -> None:
        if not self._match_entry(entry):
            return
        self.text_browser.append(self._format_entry_html(entry))
        self._update_count()
        if self._auto_scroll:
            self.text_browser.moveCursor(QTextCursor.MoveOperation.End)

    def _reload_logs(self) -> None:
        records = self.store.get_records(
            category=None if self._current_category == "all" else self._current_category,
            search=self._search_text or None,
        )
        self.text_browser.clear()
        if records:
            html = "".join(self._format_entry_html(r) for r in records)
            self.text_browser.setHtml(html)
        self._update_count(len(records))
        if self._auto_scroll:
            self.text_browser.moveCursor(QTextCursor.MoveOperation.End)

    def _update_count(self, count: Optional[int] = None) -> None:
        if count is None:
            records = self.store.get_records(
                category=None if self._current_category == "all" else self._current_category,
                search=self._search_text or None,
            )
            count = len(records)
        self.lbl_count.setText(f"共 {count} 条日志")

    def _on_category_changed(self, btn_id: int) -> None:
        mapping = {0: "all", 1: "scan", 2: "cover"}
        self._current_category = mapping.get(btn_id, "all")
        self._reload_logs()

    def _on_search_changed(self, text: str) -> None:
        self._search_text = text.strip().casefold()
        self._reload_logs()

    def _on_auto_scroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked

    def _on_clear(self) -> None:
        self.store.clear()
        self.text_browser.clear()
        self._update_count(0)

    def _on_copy(self) -> None:
        self.text_browser.selectAll()
        self.text_browser.copy()
        cursor = self.text_browser.textCursor()
        cursor.clearSelection()
        self.text_browser.setTextCursor(cursor)
