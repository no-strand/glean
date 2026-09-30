from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr


class DirectoryDialog(QDialog):
    def __init__(self, current_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(620)
        self._path = os.path.abspath(current_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)

        self.caption_label = QLabel()
        layout.addWidget(self.caption_label)

        self.path_label = QLabel()
        self.path_label.setObjectName("directoryPathLabel")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        row = QHBoxLayout()
        self.open_button = QPushButton()
        self.open_button.clicked.connect(self._open_directory)
        row.addWidget(self.open_button)
        self.change_button = QPushButton()
        self.change_button.clicked.connect(self._change_directory)
        row.addWidget(self.change_button)
        row.addStretch(1)
        self.close_button = QPushButton()
        self.close_button.clicked.connect(self.accept)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        self.retranslate_ui()
        self._refresh_path()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("directory.title"))
        self.caption_label.setText(tr("directory.current"))
        self.open_button.setText(tr("directory.open"))
        self.change_button.setText(tr("directory.change"))
        self.close_button.setText(tr("common.ok"))

    def _refresh_path(self) -> None:
        self.path_label.setText(self._path)
        self.path_label.setToolTip(self._path)

    def _open_directory(self) -> None:
        path = self._path
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            system = platform.system()
            if system == "Windows":
                os.startfile(path)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            # Main window still validates and reports errors when its own menu
            # command is used. Keep this compact settings dialog non-blocking.
            pass

    def _change_directory(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("dialog.choose_download_directory"),
            self._path,
        )
        if not selected:
            return
        self._path = os.path.abspath(selected)
        Path(self._path).mkdir(parents=True, exist_ok=True)
        self._refresh_path()

    def current_path(self) -> str:
        return self._path
