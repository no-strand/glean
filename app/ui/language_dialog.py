from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..i18n import available_locales, current_locale, tr


class LanguageDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(430)

        outer = QVBoxLayout(self)
        self.info_label = QLabel()
        self.info_label.setWordWrap(True)
        outer.addWidget(self.info_label)

        self.combo = QComboBox()
        selected = 0
        for i, (code, name) in enumerate(available_locales()):
            self.combo.addItem(name, code)
            if code == current_locale():
                selected = i
        self.combo.setCurrentIndex(selected)
        outer.addWidget(self.combo)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.cancel_button)
        self.ok_button = QPushButton()
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        row.addWidget(self.ok_button)
        outer.addLayout(row)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr("language.title"))
        self.info_label.setText(tr("language.description"))
        self.cancel_button.setText(tr("common.cancel"))
        self.ok_button.setText(tr("common.apply"))

    def selected_locale(self) -> str:
        return str(self.combo.currentData() or current_locale())
