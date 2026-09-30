from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget

from ..i18n import tr


class ClipboardDownloadPopup(QWidget):
    """Small non-modal prompt shown when a supported URL reaches the clipboard."""

    download_requested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._url = ""
        self._queue_mode = False
        self._allow_audio = False
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("Glean")
        self.setFixedWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("clipboardPopup")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        self.title_label = QLabel()
        self.title_label.setObjectName("clipboardPopupTitle")
        layout.addWidget(self.title_label)

        self.url_label = QLabel()
        self.url_label.setObjectName("clipboardPopupUrl")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.url_label)

        self.media_label = QLabel()
        self.media_label.setObjectName("clipboardPopupMediaLabel")
        layout.addWidget(self.media_label)

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(24)
        self.video_radio = QRadioButton()
        self.video_radio.setObjectName("clipboardPopupMediaOption")
        self.audio_radio = QRadioButton()
        self.audio_radio.setObjectName("clipboardPopupMediaOption")
        self.video_radio.setChecked(True)
        for option in (self.video_radio, self.audio_radio):
            option.setMinimumHeight(24)
            option.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        media_row.addWidget(self.video_radio)
        media_row.addWidget(self.audio_radio)
        media_row.addStretch(1)
        layout.addLayout(media_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("clipboardPopupCancel")
        self.cancel_button.clicked.connect(self.hide)
        buttons.addWidget(self.cancel_button)

        self.download_button = QPushButton()
        self.download_button.setObjectName("clipboardPopupAccept")
        self.download_button.clicked.connect(self._accept)
        buttons.addWidget(self.download_button)
        layout.addLayout(buttons)

        outer.addWidget(card)
        self.retranslate_ui()

    def retranslate_ui(self) -> None:
        self.title_label.setText(tr("clipboard.queue_prompt") if self._queue_mode else tr("clipboard.prompt"))
        self.media_label.setText(tr("clipboard.media_type"))
        self.video_radio.setText(tr("media.video"))
        self.audio_radio.setText(tr("media.audio"))
        self.audio_radio.setToolTip("" if self._allow_audio else tr("clipboard.audio_youtube_only"))
        self.download_button.setText(tr("clipboard.yes"))
        self.cancel_button.setText(tr("clipboard.cancel"))
        # QRadioButton can under-report its width when the custom indicator
        # changes state. Reserve explicit text space so Video/Audio never gets
        # clipped when the user switches the selected option.
        for option in (self.video_radio, self.audio_radio):
            text_width = option.fontMetrics().horizontalAdvance(option.text())
            option.setMinimumWidth(max(96, text_width + 52))

    def show_for_url(
        self,
        url: str,
        queue_mode: bool = False,
        default_media_type: str = "video",
        allow_audio: bool = False,
        display_title: str = "",
    ) -> None:
        self._url = str(url or "").strip()
        self._queue_mode = bool(queue_mode)
        self._allow_audio = bool(allow_audio)

        self.audio_radio.setEnabled(self._allow_audio)
        if self._allow_audio and default_media_type == "audio":
            self.audio_radio.setChecked(True)
        else:
            self.video_radio.setChecked(True)

        self.retranslate_ui()
        if not self._url:
            self.hide()
            return
        title = " ".join(str(display_title or "").split()).strip()
        self.url_label.setText(title)
        self.url_label.setToolTip(title)
        self.adjustSize()
        self._move_to_bottom_right()
        self.show()
        self.raise_()

    def set_resolved_title(self, url: str, title: str) -> None:
        if str(url or "").strip() != self._url:
            return
        clean_title = " ".join(str(title or "").split()).strip()
        if not clean_title:
            return
        self.url_label.setText(clean_title)
        self.url_label.setToolTip(clean_title)
        self.adjustSize()
        self._move_to_bottom_right()

    def _move_to_bottom_right(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 18
        x = area.right() - self.width() - margin + 1
        y = area.bottom() - self.height() - margin + 1
        self.move(max(area.left(), x), max(area.top(), y))

    def _accept(self) -> None:
        url = self._url
        media_type = "audio" if self._allow_audio and self.audio_radio.isChecked() else "video"
        self.hide()
        if url:
            self.download_requested.emit(url, media_type)
