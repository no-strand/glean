from __future__ import annotations

import os
import threading
import sys
from pathlib import Path

from PySide6.QtCore import QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ..config import ConfigStore
from ..platforms import classify_url, is_youtube_playlist, youtube_single_video_url, facebook_media_kind, media_kind
from ..image_conversion import IMAGE_FORMATS
from ..history import HistoryStore
from ..i18n import available_locales, set_locale, tr
from ..message_boxes import information
from ..startup import is_startup_enabled, set_startup_enabled, startup_available
from ..url_list import load_url_file
from ..version import APP_AUTHOR, APP_NAME, APP_VERSION


class MainWindow(QMainWindow):
    clipboard_title_resolved = Signal(str, str)
    media_kind_resolved = Signal(str, str)

    def __init__(self, resource_dir: str, config_store: ConfigStore | None = None) -> None:
        super().__init__()
        self.resource_dir = resource_dir
        self.config_store = config_store or ConfigStore()
        self.config = self.config_store.load()
        self.config.language = set_locale(self.config.language)
        self._startup_available = startup_available()
        if self._startup_available:
            self.config.start_with_windows = is_startup_enabled()
        self._thread: QThread | None = None
        self._worker: DownloadWorker | None = None
        self._is_downloading = False
        self._force_quit = False
        self._pending_urls: list[str] = []
        self._loaded_list_path: str | None = None
        self._active_url_count = 1
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_show_action: QAction | None = None
        self._tray_exit_action: QAction | None = None
        self.history_store = HistoryStore()
        self._history_save_pending = False
        self._clipboard = None
        self._clipboard_popup = None
        self._clipboard_download_queue: list[dict[str, object]] = []
        self._active_download_urls: set[str] = set()
        self._cancel_all_requested = False
        self._clipboard_title_cache: dict[str, str] = {}
        self._clipboard_title_inflight: set[str] = set()
        self._manual_download_plan: dict[str, object] | None = None
        self.clipboard_title_resolved.connect(self._on_clipboard_title_resolved)
        self.media_kind_resolved.connect(self._on_media_kind_resolved)

        self.setMinimumSize(850, 650)
        icon = os.path.join(self.resource_dir, "icon.ico")
        if os.path.isfile(icon):
            self.setWindowIcon(QIcon(icon))

        self._create_menu()
        self._build_ui()
        self.retranslate_ui()
        # Keep first paint fast: secondary UI/services are initialized only
        # after the Qt event loop starts.
        QTimer.singleShot(0, self._create_system_tray)
        QTimer.singleShot(0, self._schedule_incomplete_cleanup)
        QTimer.singleShot(0, self._start_clipboard_monitoring)

    def _create_menu(self) -> None:
        bar = self.menuBar()

        self.menu_open = bar.addMenu("")
        self.open_list_action = QAction(self)
        self.open_list_action.triggered.connect(self.open_download_list)
        self.menu_open.addAction(self.open_list_action)
        self.menu_open.addSeparator()
        self.exit_action = QAction(self)
        self.exit_action.triggered.connect(self.quit_application)
        self.menu_open.addAction(self.exit_action)

        self.menu_settings = bar.addMenu("")
        self.start_with_windows_action = QAction(self)
        self.start_with_windows_action.setCheckable(True)
        self.start_with_windows_action.setChecked(self.config.start_with_windows and self._startup_available)
        self.start_with_windows_action.setEnabled(self._startup_available)
        self.start_with_windows_action.toggled.connect(self._set_start_with_windows)
        self.menu_settings.addAction(self.start_with_windows_action)

        self.thumbnails_action = QAction(self)
        self.thumbnails_action.setCheckable(True)
        self.thumbnails_action.setChecked(self.config.download_thumbnails)
        self.thumbnails_action.toggled.connect(self._set_download_thumbnails)
        self.menu_settings.addAction(self.thumbnails_action)
        self.menu_settings.addSeparator()

        self.video_quality_action = QAction(self)
        self.video_quality_action.triggered.connect(self.open_video_quality)
        self.menu_settings.addAction(self.video_quality_action)

        self.audio_quality_action = QAction(self)
        self.audio_quality_action.triggered.connect(self.open_audio_quality)
        self.menu_settings.addAction(self.audio_quality_action)
        self.menu_settings.addSeparator()

        self.change_directory_action = QAction(self)
        self.change_directory_action.triggered.connect(self.change_download_directory)
        self.menu_settings.addAction(self.change_directory_action)

        self.open_folder_action = QAction(self)
        self.open_folder_action.triggered.connect(self.open_download_directory)
        self.menu_settings.addAction(self.open_folder_action)

        self.menu_help = bar.addMenu("")
        self.check_updates_action = QAction(self)
        self.check_updates_action.triggered.connect(self.check_for_updates)
        self.menu_help.addAction(self.check_updates_action)
        self.menu_help.addSeparator()
        self.language_action = QAction(self)
        self.language_action.triggered.connect(self.open_language_dialog)
        self.menu_help.addAction(self.language_action)
        self.menu_help.addSeparator()
        self.about_action = QAction(self)
        self.about_action.triggered.connect(self.show_about)
        self.menu_help.addAction(self.about_action)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        outer = QVBoxLayout(root)
        outer.setContentsMargins(36, 28, 36, 28)
        outer.addItem(QSpacerItem(0, 18, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))

        card = QFrame()
        card.setObjectName("mainCard")
        card.setMaximumWidth(850)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(42, 36, 42, 36)
        card_layout.setSpacing(20)

        # Keep the quick actions in their own row so they do not shift the
        # application title away from the true horizontal center.
        header_actions_row = QHBoxLayout()
        header_actions_row.setSpacing(12)
        header_actions_row.addStretch(1)
        self.header_folder_button = QPushButton()
        self.header_folder_button.setObjectName("headerButton")
        self.header_folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header_folder_button.setFixedSize(42, 38)
        self.header_folder_button.setIconSize(QSize(24, 24))
        folder_icon = os.path.join(self.resource_dir, "folder_open_24.png")
        if os.path.isfile(folder_icon):
            self.header_folder_button.setIcon(QIcon(folder_icon))
        self.header_folder_button.clicked.connect(self.open_download_directory)
        header_actions_row.addWidget(self.header_folder_button)
        self.history_button = QPushButton()
        self.history_button.setObjectName("headerButton")
        self.history_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.history_button.setFixedSize(42, 38)
        self.history_button.setIconSize(QSize(24, 24))
        history_icon = os.path.join(self.resource_dir, "history_24.png")
        if os.path.isfile(history_icon):
            self.history_button.setIcon(QIcon(history_icon))
        self.history_button.clicked.connect(self.open_history)
        header_actions_row.addWidget(self.history_button)
        card_layout.addLayout(header_actions_row)

        self.title_label = QLabel()
        self.title_label.setObjectName("titleLabel")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.title_label)

        self.subtitle_label = QLabel()
        self.subtitle_label.setObjectName("subtitleLabel")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.subtitle_label)

        type_row = QHBoxLayout()
        type_row.setSpacing(22)
        type_row.addStretch(1)
        self.video_check = QCheckBox()
        self.audio_check = QCheckBox()
        self.video_check.setChecked(self.config.preferred_av == "video")
        self.audio_check.setChecked(self.config.preferred_av == "audio")
        self.video_check.toggled.connect(self._on_video_toggled)
        self.audio_check.toggled.connect(self._on_audio_toggled)
        type_row.addWidget(self.video_check)
        type_row.addWidget(self.audio_check)
        type_row.addStretch(1)
        card_layout.addLayout(type_row)

        self.url_entry = QLineEdit()
        self.url_entry.setObjectName("urlEntry")
        self.url_entry.setClearButtonEnabled(True)
        self.url_entry.returnPressed.connect(self.download_or_cancel)
        self.url_entry.textEdited.connect(self._on_url_edited)
        card_layout.addWidget(self.url_entry)

        self.download_button = QPushButton()
        self.download_button.setObjectName("downloadButton")
        self.download_button.setMinimumHeight(62)
        self.download_button.clicked.connect(self.download_or_cancel)
        card_layout.addWidget(self.download_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        card_layout.addWidget(self.progress_bar)

        self.queue_label = QLabel("")
        self.queue_label.setObjectName("queueLabel")
        self.queue_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.queue_label.hide()
        card_layout.addWidget(self.queue_label)

        self.download_type_label = QLabel("")
        self.download_type_label.setObjectName("downloadTypeLabel")
        self.download_type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.download_type_label.hide()
        card_layout.addWidget(self.download_type_label)

        self.file_name_label = QLabel("")
        self.file_name_label.setObjectName("fileNameLabel")
        self.file_name_label.setWordWrap(True)
        self.file_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.file_name_label)

        # This area intentionally starts blank. It is reserved for useful
        # states/errors rather than showing an idle "Ready" message.
        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        card_layout.addWidget(self.status_label)

        centered = QHBoxLayout()
        centered.addStretch(1)
        centered.addWidget(card, 1)
        centered.addStretch(1)
        outer.addLayout(centered)
        outer.addItem(QSpacerItem(0, 18, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding))
        self.setCentralWidget(root)

    def _create_system_tray(self) -> None:
        if sys.platform != "win32" or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(self.windowIcon(), self)
        tray.setToolTip(APP_NAME)
        tray.activated.connect(self._on_tray_activated)

        menu = QMenu(self)
        menu.setObjectName("trayMenu")
        # The tray is created after the first UI translation pass. Give the
        # actions their translated text immediately so the native Windows
        # context menu never opens with blank rows.
        show_action = QAction(tr("tray.show"), self)
        show_action.triggered.connect(self.restore_from_tray)
        menu.addAction(show_action)
        menu.addSeparator()
        exit_action = QAction(tr("action.exit"), self)
        exit_action.triggered.connect(self.quit_application)
        menu.addAction(exit_action)
        tray.setContextMenu(menu)
        tray.show()

        # A tray application must stay alive while its last visible window is
        # hidden. Explicit "Exit" still calls QApplication.quit().
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

        self._tray_icon = tray
        self._tray_menu = menu
        self._tray_show_action = show_action
        self._tray_exit_action = exit_action

    def retranslate_ui(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.menu_open.setTitle(tr("menu.open"))
        self.open_list_action.setText(tr("action.open_download_list"))
        self.exit_action.setText(tr("action.exit"))
        self.menu_settings.setTitle(tr("menu.settings"))
        self.start_with_windows_action.setText(tr("action.start_with_windows"))
        self.start_with_windows_action.setStatusTip(
            "" if self._startup_available else tr("startup.installed_only")
        )
        self.thumbnails_action.setText(tr("action.download_thumbnails"))
        self.video_quality_action.setText(tr("action.video_quality"))
        self.audio_quality_action.setText(tr("action.audio_quality"))
        self.change_directory_action.setText(tr("action.change_directory"))
        self.open_folder_action.setText(tr("action.open_download_folder"))
        self.menu_help.setTitle(tr("menu.help"))
        self.check_updates_action.setText(tr("action.check_updates"))
        self.language_action.setText(tr("action.language"))
        self.about_action.setText(tr("action.about"))
        self.title_label.setText(APP_NAME)
        self.header_folder_button.setText("")
        self.header_folder_button.setToolTip(tr("action.open_download_folder_short"))
        self.header_folder_button.setAccessibleName(tr("action.open_download_folder_short"))
        self.history_button.setText("")
        self.history_button.setToolTip(tr("history.button"))
        self.history_button.setAccessibleName(tr("history.button"))
        self.subtitle_label.setText(tr("app.subtitle"))
        self.video_check.setText(tr("action.download_video"))
        self.audio_check.setText(tr("action.download_audio"))
        self.download_button.setText(tr("action.cancel") if self._is_downloading else tr("action.download"))
        if self._pending_urls:
            self.url_entry.setPlaceholderText(tr("input.list_loaded", count=len(self._pending_urls)))
            if self._loaded_list_path:
                self.file_name_label.setText(
                    tr("list.loaded", name=Path(self._loaded_list_path).name, count=len(self._pending_urls))
                )
        else:
            self.url_entry.setPlaceholderText(tr("input.url_placeholder"))
        current_urls = list(self._pending_urls)
        if not current_urls:
            current_text = self.url_entry.text().strip()
            current_urls = [current_text] if current_text else []
        self._update_download_type_label(current_urls)
        self._update_queue_label()
        if self._tray_show_action is not None:
            self._tray_show_action.setText(tr("tray.show"))
        if self._tray_exit_action is not None:
            self._tray_exit_action.setText(tr("action.exit"))
        if self._clipboard_popup is not None:
            self._clipboard_popup.retranslate_ui()

    def _start_clipboard_monitoring(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        clipboard = app.clipboard()
        self._clipboard = clipboard
        # Do not alert for a URL that was already on the clipboard before
        # Glean started; only react to subsequent clipboard changes.
        clipboard.dataChanged.connect(self._on_clipboard_changed)

    @Slot()
    def _on_clipboard_changed(self) -> None:
        if self._clipboard is None:
            return
        # Both Copy and Cut change the system clipboard. Do not permanently
        # suppress an identical URL: a new clipboard event is a new request.
        text = self._clipboard.text().strip()
        if classify_url(text) is None:
            # Some applications expose links as URI-list rather than plain text.
            mime = self._clipboard.mimeData()
            if mime is not None and mime.hasUrls():
                urls = mime.urls()
                if len(urls) == 1:
                    candidate = urls[0].toString().strip()
                    if classify_url(candidate) is not None:
                        text = candidate
        if not text:
            return
        if self._manual_download_plan is not None:
            # A manual playlist decision must not be replaced by a new
            # clipboard notification while the user is choosing.
            return

        # Keep monitoring while a worker is active. Supported URLs can be
        # confirmed and queued for sequential download instead of being lost.
        if classify_url(text) is None:
            if self._clipboard_popup is not None:
                self._clipboard_popup.hide()
            return
        # Repeated notifications must not reset choices in an open popup.
        if (self._clipboard_popup is not None and self._clipboard_popup.isVisible()
                and self._clipboard_popup.original_url == text):
            return
        self._show_clipboard_download_prompt(text)

    def _show_clipboard_download_prompt(
        self, url: str, *, manual: bool = False, allow_audio_override: bool | None = None,
    ) -> None:
        platform_key = classify_url(url)
        if platform_key is None:
            return
        if self._clipboard_popup is None:
            from .clipboard_popup import ClipboardDownloadPopup

            popup = ClipboardDownloadPopup()
            popup.download_requested.connect(self._download_clipboard_url)
            popup.cancelled.connect(self._on_popup_cancelled)
            self._clipboard_popup = popup
        allow_audio = media_kind(url) != "image"
        if allow_audio_override is not None:
            allow_audio = allow_audio and allow_audio_override
        default_media_type = self.config.preferred_av if allow_audio else "video"
        display_title = self._clipboard_title_cache.get(url) or self._clipboard_fallback_title(url)
        self._clipboard_popup.show_for_url(
            url,
            queue_mode=not manual and (self._is_downloading or bool(self._clipboard_download_queue)),
            default_media_type=default_media_type,
            allow_audio=allow_audio,
            display_title=display_title,
            default_image_format=self.config.preferred_image_format,
            default_playlist_scope=self.config.preferred_playlist_scope,
            default_mixed_media=self.config.preferred_mixed_media,
        )
        self._request_clipboard_title(url)
        if media_kind(url) == "unknown":
            def probe():
                from ..downloader import probe_media_kind
                self.media_kind_resolved.emit(url, probe_media_kind(url))
            threading.Thread(target=probe, daemon=True, name="GleanMediaProbe").start()

    @Slot(str, str)
    def _on_media_kind_resolved(self, url: str, kind: str) -> None:
        if self._clipboard_popup is not None and self._clipboard_popup.isVisible():
            self._clipboard_popup.set_media_kind(url, kind)

    def _clipboard_fallback_title(self, url: str) -> str:
        platform_key = classify_url(url)
        if platform_key == "yt":
            return tr("download.type.youtube_playlist") if is_youtube_playlist(url) else tr("download.type.youtube")
        if platform_key == "ig":
            return tr("download.type.instagram")
        if platform_key == "tt":
            return tr("download.type.tiktok")
        if platform_key == "x":
            return tr("download.type.x")
        if platform_key == "fb":
            return tr("download.type.facebook_" + facebook_media_kind(url))
        return APP_NAME

    def _request_clipboard_title(self, url: str) -> None:
        if url in self._clipboard_title_cache or url in self._clipboard_title_inflight:
            return
        self._clipboard_title_inflight.add(url)

        def resolve() -> None:
            title = ""
            try:
                from ..downloader import fetch_url_title

                title = fetch_url_title(url)
            finally:
                self.clipboard_title_resolved.emit(url, title)

        threading.Thread(target=resolve, name="GleanClipboardTitle", daemon=True).start()

    @Slot(str, str)
    def _on_clipboard_title_resolved(self, url: str, title: str) -> None:
        self._clipboard_title_inflight.discard(url)
        clean_title = " ".join(str(title or "").split()).strip()
        if clean_title:
            self._clipboard_title_cache[url] = clean_title
        else:
            clean_title = self._clipboard_fallback_title(url)
        if self._clipboard_popup is not None:
            self._clipboard_popup.set_resolved_title(url, clean_title)

    def _clipboard_job(self, url: str, requested_media_type: str | None = None) -> dict[str, object] | None:
        platform_key = classify_url(url)
        if platform_key is None:
            return None

        # The media selection and format are stored in the queued job itself.
        if media_kind(url) == "image" and not (requested_media_type or "").startswith("image:"):
            return None
        if requested_media_type and requested_media_type.startswith("image:"):
            media_type = "image"
            quality = requested_media_type.split(":", 1)[1]
            if quality not in IMAGE_FORMATS or media_kind(url) == "video":
                return None
        elif requested_media_type == "audio" and media_kind(url) != "image":
            media_type = "audio"
            quality = self.config.audio_quality
        else:
            media_type = "video" if platform_key == "yt" else "video_only"
            quality = self.config.video_quality

        return {
            "url": url,
            "media_type": media_type,
            "quality": quality,
            "thumbnails": bool(self.config.download_thumbnails) and media_type != "image",
        }

    def _queued_urls(self) -> set[str]:
        return {str(job.get("url", "")) for job in self._clipboard_download_queue}

    def _update_queue_label(self) -> None:
        count = len(self._clipboard_download_queue)
        if not count:
            self.queue_label.clear()
            self.queue_label.setToolTip("")
            self.queue_label.hide()
            return
        self.queue_label.setText(tr("queue.pending", count=count))
        self.queue_label.setToolTip("\n".join(str(job.get("url", "")) for job in self._clipboard_download_queue))
        self.queue_label.show()

    def _start_clipboard_job(self, job: dict[str, object]) -> None:
        url = str(job["url"])
        media_type = str(job["media_type"])
        quality = str(job["quality"])
        thumbnails = bool(job["thumbnails"])

        self._pending_urls.clear()
        self._loaded_list_path = None
        self.file_name_label.clear()
        self.url_entry.setPlaceholderText(tr("input.url_placeholder"))
        self.url_entry.setText(url)
        self._update_download_type_label([url])
        self._start_download([url], media_type, quality, thumbnails)

    def _start_next_queued_download(self) -> bool:
        if self._is_downloading or not self._clipboard_download_queue:
            return False
        job = self._clipboard_download_queue.pop(0)
        self._update_queue_label()
        self._start_clipboard_job(job)
        return True

    def _remember_popup_choice(self, url: str, media_type: str) -> None:
        original = self._clipboard_popup.original_url
        single = youtube_single_video_url(original)
        scope = ("single" if url == single else "playlist") if single else None
        self.config.remember_download_choice(media_type, scope)
        # Reflect the latest confirmed audio/video choice, not the media type
        # of an older queued job that happens to start later.
        if media_type in ("video", "audio"):
            for check, selected in ((self.video_check, media_type == "video"),
                                    (self.audio_check, media_type == "audio")):
                blocked = check.blockSignals(True)
                check.setChecked(selected)
                check.blockSignals(blocked)
        self._save_config()

    @Slot(str, str)
    def _download_clipboard_url(self, url: str, media_type: str) -> None:
        job = self._clipboard_job(url, media_type)
        if job is None:
            if self._manual_download_plan is not None:
                self._manual_download_plan = None
                self._set_status(tr("error.invalid_media_type"), "error")
            return
        self._remember_popup_choice(url, media_type)
        if self._manual_download_plan is not None:
            if "jobs" in self._manual_download_plan:
                self._resolve_media_job(url, media_type)
                return
            self._resolve_manual_playlist_choice(url, media_type)
            return
        if url in self._active_download_urls or url in self._queued_urls():
            return

        self._record_download_attempts([url])
        if self._is_downloading or self._clipboard_download_queue:
            self._clipboard_download_queue.append(job)
            self._update_queue_label()
            if not self._is_downloading:
                QTimer.singleShot(0, self._start_next_queued_download)
            return

        self._start_clipboard_job(job)

    @Slot(bool)
    def _on_video_toggled(self, checked: bool) -> None:
        if checked and self.audio_check.isChecked():
            self.audio_check.blockSignals(True)
            self.audio_check.setChecked(False)
            self.audio_check.blockSignals(False)
        if checked:
            self.config.remember_download_choice("video")
            self._save_config()

    @Slot(bool)
    def _on_audio_toggled(self, checked: bool) -> None:
        if checked and self.video_check.isChecked():
            self.video_check.blockSignals(True)
            self.video_check.setChecked(False)
            self.video_check.blockSignals(False)
        if checked:
            self.config.remember_download_choice("audio")
            self._save_config()

    @Slot(str)
    def _on_url_edited(self, text: str) -> None:
        if self._manual_download_plan is not None:
            # Editing the URL invalidates any playlist confirmation for the old URL.
            self._manual_download_plan = None
            if self._clipboard_popup is not None:
                self._clipboard_popup.hide()
        if self._pending_urls:
            self._pending_urls.clear()
            self._loaded_list_path = None
            self.file_name_label.clear()
            self.url_entry.setPlaceholderText(tr("input.url_placeholder"))
            self._set_status("", "normal")
        self._update_download_type_label([text.strip()] if text.strip() else [])

    def _download_type_key(self, urls: list[str]) -> str | None:
        if self._loaded_list_path:
            return "download.type.list"
        if len(urls) != 1:
            return "download.type.list" if urls else None
        url = urls[0]
        platform_key = classify_url(url)
        if platform_key == "yt":
            return "download.type.youtube_playlist" if is_youtube_playlist(url) else "download.type.youtube"
        if platform_key == "ig":
            return "download.type.instagram"
        if platform_key == "tt":
            return "download.type.tiktok"
        if platform_key == "x":
            return "download.type.x"
        if platform_key == "fb":
            return "download.type.facebook_" + facebook_media_kind(url)
        return None

    def _update_download_type_label(self, urls: list[str]) -> None:
        key = self._download_type_key(urls)
        if not key:
            self.download_type_label.clear()
            self.download_type_label.hide()
            return
        self.download_type_label.setText(tr("download.type_label", type=tr(key)))
        self.download_type_label.show()

    def _set_status(self, text: str, kind: str = "normal") -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("statusKind", kind)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _save_config(self) -> None:
        try:
            self.config_store.save(self.config)
        except OSError:
            pass

    @Slot(bool)
    def _set_start_with_windows(self, checked: bool) -> None:
        if not self._startup_available:
            self.start_with_windows_action.blockSignals(True)
            self.start_with_windows_action.setChecked(False)
            self.start_with_windows_action.blockSignals(False)
            self.config.start_with_windows = False
            self._save_config()
            self._set_status(tr("startup.installed_only"), "warning")
            return
        try:
            set_startup_enabled(bool(checked))
        except OSError as exc:
            self.start_with_windows_action.blockSignals(True)
            self.start_with_windows_action.setChecked(not checked)
            self.start_with_windows_action.blockSignals(False)
            self.config.start_with_windows = not checked
            self._save_config()
            self._set_status(tr("startup.change_failed", error=exc), "error")
            return
        self.config.start_with_windows = bool(checked)
        self._save_config()
        self._set_status(tr("startup.enabled") if checked else tr("startup.disabled"), "success")

    @Slot(bool)
    def _set_download_thumbnails(self, checked: bool) -> None:
        self.config.download_thumbnails = bool(checked)
        self._save_config()

    @Slot()
    def open_history(self) -> None:
        from .history_dialog import HistoryDialog

        dialog = HistoryDialog(self.history_store.entries(), self)
        dialog.exec()
        if dialog.clear_requested():
            self.history_store.clear()
            self._schedule_history_save()
            self._set_status(tr("history.cleared"), "success")

    def _record_download_attempts(self, urls: list[str]) -> None:
        # Persist the entire accepted batch before starting or queuing any work.
        for url in urls:
            if classify_url(url) is not None:
                self.history_store.add(url, self._clipboard_title_cache.get(url, ""))
        self._flush_history()

    @Slot(str, str)
    def _record_download_url(self, url: str, title: str) -> None:
        self.history_store.update_title(url, title)
        self._schedule_history_save()

    def _schedule_history_save(self) -> None:
        if self._history_save_pending:
            return
        self._history_save_pending = True
        QTimer.singleShot(450, self._flush_history)

    @Slot()
    def _flush_history(self) -> None:
        self._history_save_pending = False
        try:
            self.history_store.save()
        except OSError:
            pass

    @Slot()
    def open_video_quality(self) -> None:
        from .quality_dialog import QualityDialog

        dialog = QualityDialog("quality.video_title", self.config.video_quality, self)
        if dialog.exec() == QualityDialog.DialogCode.Accepted:
            self.config.video_quality = dialog.current_quality()
            self._save_config()

    @Slot()
    def open_audio_quality(self) -> None:
        from .quality_dialog import QualityDialog

        dialog = QualityDialog("quality.audio_title", self.config.audio_quality, self)
        if dialog.exec() == QualityDialog.DialogCode.Accepted:
            self.config.audio_quality = dialog.current_quality()
            self._save_config()

    @Slot()
    def change_download_directory(self) -> None:
        from .directory_dialog import DirectoryDialog

        old_path = self.config.download_path
        dialog = DirectoryDialog(old_path, self)
        if dialog.exec() != DirectoryDialog.DialogCode.Accepted:
            return
        selected = os.path.abspath(dialog.current_path())
        if selected == os.path.abspath(old_path):
            return
        self.config.download_path = selected
        Path(self.config.download_path).mkdir(parents=True, exist_ok=True)
        self._save_config()
        self._set_status(tr("status.directory_changed"), "success")

    @Slot()
    def open_download_directory(self) -> None:
        path = self.config.download_path
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                import subprocess

                command = ["open", path] if sys.platform == "darwin" else ["xdg-open", path]
                subprocess.Popen(command)
        except Exception as exc:
            self._set_status(tr("error.open_folder", error=exc), "error")

    @Slot()
    def open_download_list(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("dialog.open_download_list"),
            "",
            tr("dialog.txt_filter"),
        )
        if not path:
            return
        try:
            urls = load_url_file(path)
        except (OSError, ValueError) as exc:
            self._set_status(tr("list.read_error", error=exc), "error")
            return
        if not urls:
            self._set_status(tr("list.no_urls"), "error")
            return

        self._pending_urls = urls
        self._loaded_list_path = path
        self.url_entry.clear()
        self.url_entry.setPlaceholderText(tr("input.list_loaded", count=len(urls)))
        self.file_name_label.setText(tr("list.loaded", name=Path(path).name, count=len(urls)))
        self._update_download_type_label(urls)
        self._set_status(tr("list.ready", count=len(urls)), "success")

    @Slot()
    def open_language_dialog(self) -> None:
        from .language_dialog import LanguageDialog

        dialog = LanguageDialog(self)
        if dialog.exec() != LanguageDialog.DialogCode.Accepted:
            return
        selected = dialog.selected_locale()
        if selected == self.config.language:
            return
        self.config.language = set_locale(selected)
        self._save_config()
        self.retranslate_ui()
        name = next((name for code, name in available_locales() if code == selected), selected)
        self._set_status(tr("language.changed", name=name), "success")

    @Slot()
    def check_for_updates(self) -> None:
        # The updater is imported only when requested so normal startup stays
        # as light as before.
        from .update_dialog import UpdateDialog

        dialog = UpdateDialog(self)
        dialog.install_requested.connect(self._install_downloaded_update)
        dialog.exec()

    @Slot(str)
    def _install_downloaded_update(self, package_path: str) -> None:
        from ..updater import UpdateError, schedule_update_install

        try:
            schedule_update_install(package_path)
        except UpdateError as exc:
            dialog = self.sender()
            if hasattr(dialog, "install_failed"):
                dialog.install_failed(str(exc))
            return
        # The privileged helper waits until this process is gone, applies the
        # release package, and reopens the updated Glean automatically.
        self.quit_application()

    @Slot()
    def show_about(self) -> None:
        information(
            self,
            tr("about.title"),
            tr("about.body", version=APP_VERSION, author=APP_AUTHOR),
            icon_path=os.path.join(self.resource_dir, "icon.ico"),
        )

    def _schedule_incomplete_cleanup(self) -> None:
        threading.Thread(target=self._clean_incomplete_files, name="GleanCleanup", daemon=True).start()

    def _clean_incomplete_files(self) -> None:
        directory = Path(self.config.download_path)
        if not directory.is_dir():
            return
        try:
            entries = directory.iterdir()
        except OSError:
            return
        for path in entries:
            if not path.is_file():
                continue
            # .webp is valid downloaded media for social galleries and must
            # never be treated as a disposable thumbnail artifact.
            if path.name.lower().endswith((".part", ".ytdl")):
                try:
                    path.unlink()
                except OSError:
                    pass

    def _ffmpeg_path(self) -> str:
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        else:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "ffmpeg.exe")

    @Slot()
    def download_or_cancel(self) -> None:
        if self._is_downloading:
            self._request_cancel()
            return
        if self._manual_download_plan is not None:
            return
        if self._clipboard_download_queue:
            self._start_next_queued_download()
            return

        urls = list(self._pending_urls)
        if not urls:
            url = self.url_entry.text().strip()
            if not url:
                self._set_status(tr("error.enter_url"), "error")
                return
            urls = [url]

        if any(classify_url(url) not in (None, "yt") for url in urls):
            if any(classify_url(url) is None for url in urls):
                self._set_status(tr("error.invalid_url"), "error")
                return
            self._manual_download_plan = {"urls": urls, "next_index": 0, "jobs": [], "media_type": "audio" if self.audio_check.isChecked() else "video"}
            self._prompt_next_media_job()
            return

        if not self.video_check.isChecked() and not self.audio_check.isChecked():
            self._set_status(tr("error.select_media"), "error")
            return

        for url in urls:
            if classify_url(url) is None:
                self._set_status(tr("error.invalid_url"), "error")
                return

        media_type = "video" if self.video_check.isChecked() else "audio"
        if media_type == "audio" and any(classify_url(url) != "yt" for url in urls):
            self._set_status(tr("list.audio_youtube_only"), "error")
            return

        quality = self.config.video_quality if media_type == "video" else self.config.audio_quality
        if any(is_youtube_playlist(url) for url in urls):
            self._manual_download_plan = {
                "urls": urls,
                "resolved": [],
                "next_index": 0,
                "media_type": media_type,
                "quality": quality,
                "thumbnails": self.config.download_thumbnails,
            }
            self._prompt_next_manual_playlist()
            return
        if not self._pending_urls:
            self.url_entry.clear()
        self._record_download_attempts(urls)
        self._start_download(urls, media_type, quality, self.config.download_thumbnails)

    def _prompt_next_media_job(self) -> None:
        plan = self._manual_download_plan
        index = plan['next_index']
        if index < len(plan['urls']):
            self._show_clipboard_download_prompt(plan['urls'][index], manual=True)
            return
        jobs = plan['jobs']
        self._manual_download_plan = None
        self._record_download_attempts([job['url'] for job in jobs])
        self._clipboard_download_queue.extend(jobs)
        self._update_queue_label()
        self._start_next_queued_download()

    def _resolve_media_job(self, url: str, media_type: str) -> None:
        plan = self._manual_download_plan
        original = plan['urls'][plan['next_index']]
        if url not in (original, youtube_single_video_url(original)):
            self._manual_download_plan = None
            return
        job = self._clipboard_job(url, media_type)
        if job is None:
            self._manual_download_plan = None
            self._set_status(tr('error.invalid_media_type'), 'error')
            return
        plan['jobs'].append(job)
        plan['next_index'] += 1
        self._prompt_next_media_job()

    def _prompt_next_manual_playlist(self) -> None:
        """Ask about each playlist in a URL list before starting any download."""
        plan = self._manual_download_plan
        if plan is None:
            return
        urls = plan["urls"]
        resolved = plan["resolved"]
        assert isinstance(urls, list) and isinstance(resolved, list)
        index = int(plan["next_index"])
        while index < len(urls):
            url = str(urls[index])
            if is_youtube_playlist(url):
                plan["next_index"] = index
                self._show_clipboard_download_prompt(
                    url,
                    manual=True,
                    allow_audio_override=all(classify_url(item) == "yt" for item in urls),
                )
                return
            resolved.append(url)
            index += 1
        self._manual_download_plan = None
        if not self._pending_urls:
            self.url_entry.clear()
        media_type = str(plan["media_type"])
        quality = self.config.video_quality if media_type == "video" else self.config.audio_quality
        self._record_download_attempts(resolved)
        self._start_download(resolved, media_type, quality, bool(plan["thumbnails"]))

    def _resolve_manual_playlist_choice(self, url: str, media_type: str) -> None:
        plan = self._manual_download_plan
        if plan is None:
            return
        urls = plan["urls"]
        resolved = plan["resolved"]
        assert isinstance(urls, list) and isinstance(resolved, list)
        index = int(plan["next_index"])
        original = str(urls[index])
        # Never accept a URL outside the original user's playlist/video.
        if url not in {original, youtube_single_video_url(original)}:
            self._manual_download_plan = None
            return
        resolved.append(url)
        plan["next_index"] = index + 1
        if len(urls) == 1 or all(classify_url(item) == "yt" for item in urls):
            plan["media_type"] = media_type
        self._prompt_next_manual_playlist()

    @Slot()
    def _on_popup_cancelled(self) -> None:
        if self._manual_download_plan is not None:
            self._manual_download_plan = None
            self._set_status(tr("download.cancelled"), "warning")

    def _set_download_controls_enabled(self, enabled: bool) -> None:
        self.open_list_action.setEnabled(enabled)
        self.thumbnails_action.setEnabled(enabled)
        self.video_quality_action.setEnabled(enabled)
        self.audio_quality_action.setEnabled(enabled)
        self.change_directory_action.setEnabled(enabled)
        self.video_check.setEnabled(enabled)
        self.audio_check.setEnabled(enabled)
        self.url_entry.setEnabled(enabled)

    def _start_download(self, urls: list[str], media_type: str, quality: str, thumbnails: bool) -> None:
        # An unrelated clipboard confirmation can remain open while the queue advances.
        self._is_downloading = True
        self._cancel_all_requested = False
        self._active_download_urls = set(urls)
        self._active_url_count = max(1, len(urls))
        self._update_download_type_label(urls)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        if len(urls) == 1:
            self.file_name_label.clear()
        self._set_status(tr("status.starting"), "working")
        self.download_button.setText(tr("action.cancel"))
        self._set_download_controls_enabled(False)

        # Import the heavy download engines only when they are first needed.
        # This keeps normal application startup fast.
        from ..worker import DownloadWorker

        thread = QThread(self)
        worker = DownloadWorker(
            urls=urls,
            media_type=media_type,
            quality=quality,
            thumbnails=thumbnails,
            download_dir=self.config.download_path,
            ffmpeg_path=self._ffmpeg_path(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_progress)
        worker.message.connect(self.file_name_label.setText)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.completed.connect(self._on_completed)
        worker.url_completed.connect(self._record_download_url)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _request_cancel(self) -> None:
        if self._worker is None:
            return
        # The main Cancel button means cancel the current operation as a whole,
        # including any clipboard items waiting behind it.
        self._cancel_all_requested = True
        self._clipboard_download_queue.clear()
        self._update_queue_label()
        self.download_button.setEnabled(False)
        self._set_status(tr("status.cancelling"), "warning")
        self._worker.request_cancel()

    @Slot(dict)
    def _on_progress(self, data: dict) -> None:
        status = data.get("status")
        if status == "gallery":
            self.progress_bar.setRange(0, 0)
            count = int(data.get("gallery_item", 0) or 0)
            if count:
                self._set_status(tr("status.gallery_item", count=count), "working")
            else:
                self._set_status(tr("status.gallery_scanning"), "working")
            return

        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 1000)

        if status == "downloading":
            progress = float(data.get("progress", 0.0))
            self.progress_bar.setValue(round(progress * 1000))
            batch_total = int(data.get("batch_total", 1) or 1)
            if batch_total > 1:
                batch_index = int(data.get("batch_index", 1) or 1)
                text = tr(
                    "status.downloading_list",
                    current=batch_index,
                    total=batch_total,
                    percent=f"{progress * 100:.2f}",
                )
            else:
                current = int(data.get("current_item", 1))
                total = int(data.get("total_items", 1))
                if total > 1:
                    text = tr(
                        "status.downloading_item",
                        current=current,
                        total=total,
                        percent=f"{progress * 100:.2f}",
                    )
                else:
                    text = tr("status.downloading", percent=f"{progress * 100:.2f}")
            speed = data.get("speed")
            remaining = data.get("time")
            if speed or remaining:
                text += f"  •  {speed or ''}  •  {remaining or ''}"
            self._set_status(text, "working")
        elif status == "processing":
            self._set_status(tr("status.processing"), "working")
        elif status in {"finished", "finalized"}:
            self.progress_bar.setValue(round(float(data.get("progress", 1.0)) * 1000))

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.progress_bar.setRange(0, 1000)
        self._set_status(tr("error.download_failed"), "error")
        self.file_name_label.setText(message)

    @Slot()
    def _on_cancelled(self) -> None:
        self.progress_bar.setRange(0, 1000)
        self._set_status(tr("download.cancelled"), "warning")
        self.file_name_label.clear()

    @Slot()
    def _on_completed(self) -> None:
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(1000)
        if self._active_url_count > 1:
            self._set_status(tr("list.completed", count=self._active_url_count), "success")
        else:
            self._set_status(tr("status.completed"), "success")

    @Slot()
    def _thread_finished(self) -> None:
        self._is_downloading = False
        self._active_download_urls.clear()
        self._worker = None
        self._thread = None
        if self._force_quit:
            if self._tray_icon is not None:
                self._tray_icon.hide()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return

        if not self._cancel_all_requested and self._clipboard_download_queue:
            # One worker at a time: start the next confirmed clipboard URL only
            # after the previous worker has fully released its thread.
            QTimer.singleShot(0, self._start_next_queued_download)
            return

        self._cancel_all_requested = False
        self.download_button.setText(tr("action.download"))
        self.download_button.setEnabled(True)
        self._set_download_controls_enabled(True)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    @Slot()
    def restore_from_tray(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot()
    def quit_application(self) -> None:
        self._force_quit = True
        self._clipboard_download_queue.clear()
        self._update_queue_label()
        self._save_config()
        self._flush_history()
        if self._is_downloading and self._worker is not None:
            self.download_button.setEnabled(False)
            self._set_status(tr("status.closing_download"), "warning")
            self._worker.request_cancel()
            return
        if self._clipboard_popup is not None:
            self._clipboard_popup.hide()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_config()
        self._flush_history()
        if not self._force_quit and self._tray_icon is not None:
            event.ignore()
            self.hide()
            return

        if self._is_downloading and self._worker is not None:
            event.ignore()
            self._force_quit = True
            self._set_status(tr("status.closing_download"), "warning")
            self._worker.request_cancel()
            return

        if self._tray_icon is not None:
            self._tray_icon.hide()
        event.accept()
