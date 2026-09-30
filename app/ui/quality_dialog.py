from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr

QUALITY_CODES = ("Alta", "Média", "Baixa")
QUALITY_KEYS = {
    "Alta": "quality.high",
    "Média": "quality.medium",
    "Baixa": "quality.low",
}


class QualityDialog(QDialog):
    """Quality selector using exclusive checkbox rows rather than a combo box."""

    def __init__(self, title_key: str, current_quality: str, parent=None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(420)
        self._title_key = title_key
        self._checks: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)

        self.label = QLabel()
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for code in QUALITY_CODES:
            check = QCheckBox()
            check.setCursor(Qt.CursorShape.PointingHandCursor)
            check.setChecked(code == current_quality)
            self.group.addButton(check)
            self._checks[code] = check
            layout.addWidget(check)

        if not any(check.isChecked() for check in self._checks.values()):
            self._checks["Alta"].setChecked(True)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.apply_button = QPushButton()
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self.accept)
        buttons.addWidget(self.apply_button)
        layout.addLayout(buttons)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.setWindowTitle(tr(self._title_key))
        self.label.setText(tr("quality.choose"))
        for code, check in self._checks.items():
            check.setText(tr(QUALITY_KEYS[code]))
        self.cancel_button.setText(tr("common.cancel"))
        self.apply_button.setText(tr("common.apply"))

    def current_quality(self) -> str:
        for code, check in self._checks.items():
            if check.isChecked():
                return code
        return "Alta"
