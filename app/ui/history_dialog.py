from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


class HistoryDialog(QDialog):
    def __init__(self, entries: list[dict[str, str]], parent=None) -> None:
        super().__init__(parent)
        self._entries = entries
        self._clear_requested = False
        self.setModal(True)
        self.resize(760, 560)
        self.setMinimumSize(620, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 18)
        layout.setSpacing(12)

        self.header = QLabel()
        self.header.setObjectName("historyHeader")
        layout.addWidget(self.header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        self.rows = QVBoxLayout(content)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(8)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        if entries:
            for entry in entries:
                self._add_row(entry)
        else:
            empty = QLabel(tr("history.empty"))
            empty.setObjectName("secondaryLabel")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.addWidget(empty)
        self.rows.addStretch(1)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton()
        self.clear_button.setEnabled(bool(entries))
        self.clear_button.clicked.connect(self._request_clear)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.retranslate_ui()

    def _add_row(self, entry: dict[str, str]) -> None:
        url = str(entry.get("url") or "")
        title = " ".join(str(entry.get("title") or "").split()).strip()
        timestamp = str(entry.get("timestamp") or "")
        row = QWidget()
        row.setObjectName("historyRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(12, 9, 10, 9)
        h.setSpacing(10)
        text_box = QVBoxLayout()
        label = QLabel(url)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        text_box.addWidget(label)
        formatted_date = self._format_timestamp(timestamp)
        metadata = f"{title} - {formatted_date}" if title and formatted_date else (title or formatted_date)
        date_label = QLabel(metadata)
        date_label.setObjectName("secondaryLabel")
        date_label.setWordWrap(True)
        text_box.addWidget(date_label)
        h.addLayout(text_box, 1)
        copy_button = QPushButton(tr("history.copy"))
        copy_button.setProperty("historyUrl", url)
        copy_button.clicked.connect(lambda _=False, value=url: QGuiApplication.clipboard().setText(value))
        h.addWidget(copy_button)
        self.rows.addWidget(row)

    @staticmethod
    def _format_timestamp(value: str) -> str:
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except ValueError:
            return value

    def _request_clear(self) -> None:
        self._clear_requested = True
        self.accept()

    def clear_requested(self) -> bool:
        return self._clear_requested

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("history.title"))
        self.header.setText(tr("history.header", count=len(self._entries)))
        self.clear_button.setText(tr("history.clear"))
        self.close_button.setText(tr("common.ok"))
