from __future__ import annotations

from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy, QVBoxLayout, QWidget

from ..i18n import tr
from ..platforms import is_youtube_playlist, youtube_single_video_url, media_kind
from ..image_conversion import IMAGE_FORMATS


class ClipboardDownloadPopup(QWidget):
    """Small non-modal prompt shown when a supported URL reaches the clipboard."""

    download_requested = Signal(str, str)
    cancelled = Signal()

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
        self._kind = "video"
        self._default_av = "video"
        self._single_video_url: str | None = None
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

        # Keep playlist scope distinct from the Video/Audio media selection.
        # Putting the two radio groups in separate parent widgets ensures Qt's
        # auto-exclusive radio behavior does not mix the two choices.
        self.playlist_scope_widget = QWidget(card)
        scope_layout = QVBoxLayout(self.playlist_scope_widget)
        scope_layout.setContentsMargins(0, 4, 0, 4)
        scope_layout.setSpacing(7)
        self.playlist_notice = QLabel()
        self.playlist_notice.setObjectName("clipboardPopupPlaylistNotice")
        self.playlist_notice.setWordWrap(True)
        scope_layout.addWidget(self.playlist_notice)
        self.single_video_radio = QRadioButton()
        self.single_video_radio.setObjectName("clipboardPopupScopeOption")
        self.playlist_radio = QRadioButton()
        self.playlist_radio.setObjectName("clipboardPopupScopeOption")
        scope_layout.addWidget(self.single_video_radio)
        scope_layout.addWidget(self.playlist_radio)
        self.playlist_single_url_label = QLabel()
        self.playlist_single_url_label.setObjectName("clipboardPopupVideoUrl")
        self.playlist_single_url_label.setWordWrap(True)
        scope_layout.addWidget(self.playlist_single_url_label)
        self.single_video_radio.setChecked(True)  # Safe initial choice before preferences are loaded.
        layout.addWidget(self.playlist_scope_widget)
        self.playlist_scope_widget.hide()

        self.media_label = QLabel()
        self.media_label.setObjectName("clipboardPopupMediaLabel")
        layout.addWidget(self.media_label)

        media_row = QHBoxLayout()
        media_row.setContentsMargins(0, 0, 0, 0)
        media_row.setSpacing(8)
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
        self.image_radio = QRadioButton()
        media_row.addWidget(self.image_radio)
        media_row.addStretch(1)
        layout.addLayout(media_row)
        self.image_format = QComboBox()
        for fmt in IMAGE_FORMATS:
            self.image_format.addItem(fmt.upper(), fmt)
        layout.addWidget(self.image_format)
        self.image_radio.toggled.connect(self.image_format.setVisible)
        self.image_format.hide()

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch(1)

        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("clipboardPopupCancel")
        self.cancel_button.clicked.connect(self._cancel)
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
        self.playlist_notice.setText(
            tr("clipboard.playlist_choose") if self._single_video_url else tr("clipboard.playlist_only")
        )
        self.single_video_radio.setText(tr("clipboard.playlist_single_video"))
        self.playlist_radio.setText(tr("clipboard.playlist_entire"))
        self.playlist_single_url_label.setText(
            tr("clipboard.playlist_single_link", url=self._single_video_url)
            if self._single_video_url else ""
        )
        self.playlist_single_url_label.setToolTip(self._single_video_url or "")
        self.media_label.setText(tr("clipboard.kind_" + self._kind))
        self.image_radio.setText(tr("media.image"))
        self.image_format.setItemText(0, tr("image.original"))
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
        default_image_format: str = "original",
        default_playlist_scope: str = "single",
        default_mixed_media: str = "video",
    ) -> None:
        self._url = str(url or "").strip()
        self._queue_mode = bool(queue_mode)
        self._allow_audio = bool(allow_audio)
        is_playlist = is_youtube_playlist(self._url)
        self._single_video_url = youtube_single_video_url(self._url)
        self.playlist_scope_widget.setVisible(is_playlist)
        self.single_video_radio.setVisible(self._single_video_url is not None)
        self.playlist_radio.setVisible(self._single_video_url is not None)
        self.playlist_single_url_label.setVisible(self._single_video_url is not None)
        if self._single_video_url and default_playlist_scope == "playlist":
            self.playlist_radio.setChecked(True)
        else:
            self.single_video_radio.setChecked(True)

        self.image_format.setCurrentIndex(max(0, self.image_format.findData(default_image_format)))
        self._kind = media_kind(self._url)
        self._default_av = "audio" if self._allow_audio and default_media_type == "audio" else "video"
        self.audio_radio.setEnabled(self._allow_audio)
        selection = default_mixed_media if self._kind in ("unknown", "mixed") else self._default_av
        if self._kind == "image" or (selection == "image" and self._kind in ("unknown", "mixed")):
            self.image_radio.setChecked(True)
        elif self._allow_audio and selection == "audio":
            self.audio_radio.setChecked(True)
        else:
            self.video_radio.setChecked(True)

        self.set_media_kind(self._url, self._kind)
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

    @property
    def original_url(self) -> str:
        return self._url

    def set_media_kind(self, url: str, kind: str) -> None:
        if url != self._url:
            return
        self._kind = kind
        image = kind == 'image'
        ambiguous = kind in ('unknown', 'mixed')
        self.video_radio.setVisible(not image)
        self.audio_radio.setVisible(not image)
        self.image_radio.setVisible(image or ambiguous)
        if image:
            self.image_radio.setChecked(True)
        elif not ambiguous and self.image_radio.isChecked():
            if self._allow_audio and self._default_av == "audio":
                self.audio_radio.setChecked(True)
            else:
                self.video_radio.setChecked(True)
        self.image_format.setVisible(self.image_radio.isChecked())
        self.retranslate_ui()
        self.adjustSize()
        self._move_to_bottom_right()

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

    def _cancel(self) -> None:
        self.hide()
        self.cancelled.emit()

    def _accept(self) -> None:
        url = self._url
        if self._single_video_url and self.single_video_radio.isChecked():
            url = self._single_video_url
        media_type = "audio" if self._allow_audio and self.audio_radio.isChecked() else "video"
        if getattr(self, "image_radio", None) is not None and self.image_radio.isChecked():
            media_type = "image:" + self.image_format.currentData()
        self.hide()
        if url:
            self.download_requested.emit(url, media_type)
