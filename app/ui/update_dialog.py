from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr
from ..updater import (
    GITHUB_RELEASES_URL,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCancelled,
    UpdateError,
    download_release_asset,
    fetch_latest_release,
    is_newer_version,
    select_update_asset,
    update_download_path,
    versions_equal,
)
from ..version import APP_VERSION


class _CheckWorker(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        try:
            self.succeeded.emit(fetch_latest_release())
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("unknown_error")
        finally:
            self.finished.emit()


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, asset: ReleaseAsset) -> None:
        super().__init__()
        self.asset = asset
        self._cancel_event = threading.Event()

    @Slot()
    def run(self) -> None:
        try:
            destination = update_download_path(self.asset)
            path = download_release_asset(
                self.asset,
                destination,
                self._cancel_event,
                lambda received, total: self.progress.emit(received, total),
            )
            self.succeeded.emit(str(path))
        except UpdateCancelled:
            self.cancelled.emit()
        except UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("download_failed")
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancel_event.set()


class UpdateDialog(QDialog):
    install_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("updateDialog")
        self.setModal(True)
        self.setMinimumWidth(500)
        self.setWindowTitle(tr("update.title"))

        self._release: ReleaseInfo | None = None
        self._asset: ReleaseAsset | None = None
        self._check_thread: QThread | None = None
        self._check_worker: _CheckWorker | None = None
        self._download_thread: QThread | None = None
        self._download_worker: _DownloadWorker | None = None
        self._downloading = False
        self._ready_package_path = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(14)

        self.heading = QLabel(tr("update.heading"))
        self.heading.setObjectName("updateHeading")
        layout.addWidget(self.heading)

        self.current_label = QLabel(tr("update.current_version", version=APP_VERSION))
        self.current_label.setObjectName("secondaryLabel")
        layout.addWidget(self.current_label)

        self.latest_label = QLabel(tr("update.latest_version_checking"))
        self.latest_label.setObjectName("secondaryLabel")
        layout.addWidget(self.latest_label)

        self.status_label = QLabel(tr("update.checking"))
        self.status_label.setObjectName("updateStatusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.hide()
        layout.addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.addStretch(1)

        self.release_page_button = QPushButton(tr("update.open_release"))
        self.release_page_button.clicked.connect(self._open_release_page)
        self.release_page_button.hide()
        buttons.addWidget(self.release_page_button)

        self.download_button = QPushButton(tr("update.download_and_install"))
        self.download_button.setObjectName("primaryButton")
        self.download_button.clicked.connect(self._start_download)
        self.download_button.hide()
        buttons.addWidget(self.download_button)

        self.cancel_button = QPushButton(tr("common.cancel"))
        self.cancel_button.clicked.connect(self._cancel_download)
        self.cancel_button.hide()
        buttons.addWidget(self.cancel_button)

        self.close_button = QPushButton(tr("update.close"))
        self.close_button.clicked.connect(self.close)
        buttons.addWidget(self.close_button)

        layout.addLayout(buttons)
        self._start_check()

    def _start_check(self) -> None:
        self.status_label.setText(tr("update.checking"))
        self.latest_label.setText(tr("update.latest_version_checking"))
        self.download_button.hide()
        self.release_page_button.hide()

        thread = QThread(self)
        worker = _CheckWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._check_succeeded)
        worker.failed.connect(self._check_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._check_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._check_thread = thread
        self._check_worker = worker
        thread.start()

    @Slot(object)
    def _check_succeeded(self, release: ReleaseInfo) -> None:
        self._release = release
        self._asset = select_update_asset(release)
        self.latest_label.setText(tr("update.latest_version", version=release.version))
        self.release_page_button.show()

        if is_newer_version(release.version, APP_VERSION):
            if self._asset is None:
                self.status_label.setText(tr("update.available_no_asset", version=release.version))
                self.download_button.hide()
            else:
                self.status_label.setText(tr("update.available", version=release.version))
                self.download_button.setText(tr("update.download_and_install"))
                self.download_button.show()
        elif versions_equal(release.version, APP_VERSION):
            self.status_label.setText(tr("update.up_to_date"))
            self.download_button.hide()
        else:
            self.status_label.setText(tr("update.local_newer", version=APP_VERSION))
            self.download_button.hide()

    @Slot(str)
    def _check_failed(self, code: str) -> None:
        self.latest_label.setText(tr("update.latest_version_unknown"))
        key = {
            "no_releases": "update.no_releases",
            "github_rate_limit": "update.rate_limit",
            "network_error": "update.network_error",
            "invalid_response": "update.invalid_response",
            "release_without_version": "update.invalid_response",
        }.get(code, "update.check_failed")
        self.status_label.setText(tr(key))
        self.release_page_button.show()

    @Slot()
    def _check_thread_finished(self) -> None:
        self._check_thread = None
        self._check_worker = None

    @Slot()
    def _start_download(self) -> None:
        if self._downloading or self._asset is None:
            return
        self._downloading = True
        self._ready_package_path = ""
        self.download_button.hide()
        self.release_page_button.hide()
        self.close_button.setEnabled(False)
        self.cancel_button.show()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.show()
        self.status_label.setText(tr("update.downloading", name=self._asset.name))

        thread = QThread(self)
        worker = _DownloadWorker(self._asset)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._download_progress)
        worker.succeeded.connect(self._download_succeeded)
        worker.failed.connect(self._download_failed)
        worker.cancelled.connect(self._download_cancelled)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._download_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._download_thread = thread
        self._download_worker = worker
        thread.start()

    @Slot(int, int)
    def _download_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            percent = max(0, min(100, round((received / total) * 100)))
            self.progress.setValue(percent)
            self.progress.setFormat(f"{percent}%")
        else:
            self.progress.setRange(0, 0)

    @Slot(str)
    def _download_succeeded(self, path: str) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.progress.setFormat("100%")
        self.status_label.setText(tr("update.download_complete"))
        self.cancel_button.hide()
        self.close_button.setEnabled(False)
        # Wait for the network thread to finish completely before asking the
        # main window to close the application for installation.
        self._ready_package_path = path

    @Slot(str)
    def _download_failed(self, code: str) -> None:
        key = {
            "checksum_failed": "update.checksum_failed",
            "download_failed": "update.download_failed",
        }.get(code, "update.download_failed")
        self.status_label.setText(tr(key))
        self._restore_after_download()

    @Slot()
    def _download_cancelled(self) -> None:
        self.status_label.setText(tr("update.download_cancelled"))
        self.progress.hide()
        self._restore_after_download()

    def _restore_after_download(self) -> None:
        self._downloading = False
        self.cancel_button.setEnabled(True)
        self.cancel_button.hide()
        self.close_button.setEnabled(True)
        self.release_page_button.show()
        if self._asset is not None:
            self.download_button.show()

    def install_failed(self, code: str) -> None:
        key = {
            "automatic_update_windows_only": "update.install_windows_only",
            "automatic_update_frozen_only": "update.install_compiled_only",
            "unsupported_package": "update.unsupported_package",
            "elevation_cancelled": "update.elevation_cancelled",
        }.get(code, "update.install_failed")
        self.status_label.setText(tr(key))
        self.progress.hide()
        self._restore_after_download()

    @Slot()
    def _download_thread_finished(self) -> None:
        self._download_thread = None
        self._download_worker = None
        if self._ready_package_path:
            path = self._ready_package_path
            self._ready_package_path = ""
            # MainWindow schedules the privileged helper and then exits Glean.
            self.install_requested.emit(path)

    @Slot()
    def _cancel_download(self) -> None:
        if self._download_worker is None:
            return
        self.cancel_button.setEnabled(False)
        self.status_label.setText(tr("update.cancelling"))
        self._download_worker.cancel()

    @Slot()
    def _open_release_page(self) -> None:
        url = self._release.html_url if self._release is not None else GITHUB_RELEASES_URL
        QDesktopServices.openUrl(QUrl(url))

    def closeEvent(self, event) -> None:
        if self._downloading and self._download_worker is not None:
            self._download_worker.cancel()
            event.ignore()
            return
        super().closeEvent(event)
