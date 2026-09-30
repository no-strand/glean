from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from .i18n import tr


def information(parent, title: str, text: str, icon_path: str | None = None) -> int:
    box = QMessageBox(parent)
    if icon_path:
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            box.setWindowIcon(icon)
            box.setIconPixmap(icon.pixmap(64, 64))
        else:
            box.setIcon(QMessageBox.Icon.Information)
    else:
        box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    button = box.button(QMessageBox.StandardButton.Ok)
    if button is not None:
        button.setText(tr("common.ok"))
    return box.exec()


def critical(parent, title: str, text: str) -> int:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    button = box.button(QMessageBox.StandardButton.Ok)
    if button is not None:
        button.setText(tr("common.ok"))
    return box.exec()
