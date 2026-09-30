from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .downloader import DownloadCancelled, GleanDownloader
from .i18n import tr


class DownloadWorker(QObject):
    progress = Signal(dict)
    message = Signal(str)
    failed = Signal(str)
    completed = Signal()
    cancelled = Signal()
    finished = Signal()
    url_completed = Signal(str, str)

    def __init__(
        self,
        urls: str | list[str],
        media_type: str,
        quality: str,
        thumbnails: bool,
        download_dir: str,
        ffmpeg_path: str,
    ) -> None:
        super().__init__()
        self.urls = [urls] if isinstance(urls, str) else list(urls)
        self.media_type = media_type
        self.quality = quality
        self.thumbnails = thumbnails
        self._batch_index = 0
        self._batch_total = max(1, len(self.urls))
        self.engine = GleanDownloader(
            download_dir,
            ffmpeg_path,
            callback_progress=self._emit_progress,
            message_callback=self.message.emit,
        )

    def _emit_progress(self, data: dict) -> None:
        payload = dict(data)
        local_progress = float(payload.get("progress", 0.0) or 0.0)
        if self._batch_total > 1:
            overall = (self._batch_index + max(0.0, min(local_progress, 1.0))) / self._batch_total
            payload["progress"] = max(0.0, min(overall, 1.0))
            payload["batch_index"] = self._batch_index + 1
            payload["batch_total"] = self._batch_total
        self.progress.emit(payload)

    @Slot()
    def run(self) -> None:
        try:
            if not self.urls:
                raise ValueError(tr("list.no_urls"))
            for index, url in enumerate(self.urls):
                self._batch_index = index
                self.engine._check_cancelled()
                if self._batch_total > 1:
                    self.message.emit(tr("list.downloading_item", current=index + 1, total=self._batch_total))
                self.engine.download(url, self.media_type, self.quality, self.thumbnails)
                self.url_completed.emit(url, self.engine.history_title(url))
        except DownloadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            text = str(exc).strip() or exc.__class__.__name__
            self.failed.emit(text)
        else:
            self.completed.emit()
        finally:
            self.finished.emit()

    def request_cancel(self) -> None:
        self.engine.cancel_event.set()
