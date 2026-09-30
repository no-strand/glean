from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .i18n import tr
from .platforms import classify_url

class DownloadCancelled(Exception):
    """Raised when the user requests cancellation."""


# Third-party download engines are deliberately imported on demand. This keeps
# the GUI/worker lightweight and avoids importing gallery-dl for YouTube-only
# sessions (and yt-dlp for operations that only need gallery-dl).
yt_dlp = None
gallery_config = None
gallery_exception = None
_GalleryMediaJob = None
_RUNTIME_IMPORT_LOCK = threading.RLock()


ProgressCallback = Callable[[dict], None]
MessageCallback = Callable[[str], None]

_IMAGE_EXTENSIONS = {
    "jpg", "jpeg", "jpe", "jfif", "png", "webp", "gif", "bmp", "avif", "heic", "heif",
}
_VIDEO_EXTENSIONS = {
    "mp4", "mkv", "webm", "mov", "avi", "flv", "m4v", "ts", "3gp", "wmv",
}
_GALLERY_CONFIG_LOCK = threading.RLock()
_GALLERY_CONFIG_LOADED = False


class _QuietLogger:
    def debug(self, _msg: str) -> None:
        pass

    def info(self, _msg: str) -> None:
        pass

    def warning(self, _msg: str) -> None:
        pass

    def error(self, _msg: str) -> None:
        pass


def _extension_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    return Path(path).suffix.lower().lstrip(".")


def _get_ytdlp():
    global yt_dlp
    if yt_dlp is not None:
        return yt_dlp
    with _RUNTIME_IMPORT_LOCK:
        if yt_dlp is None:
            try:
                import yt_dlp as module
            except ImportError as exc:
                raise RuntimeError(tr("error.ytdlp_missing")) from exc
            yt_dlp = module
    return yt_dlp


def fetch_url_title(url: str, timeout: float = 6.0) -> str:
    """Resolve a human-readable title without downloading media.

    This runs safely from a background thread and intentionally reuses the
    lazy yt-dlp runtime so clipboard monitoring never slows application start.
    An empty string means metadata could not be resolved; callers should use a
    platform-specific fallback rather than exposing the raw URL.
    """
    url = str(url or "").strip()
    if classify_url(url) is None:
        return ""

    try:
        module = _get_ytdlp()
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "logger": _QuietLogger(),
            "socket_timeout": max(2.0, float(timeout)),
            "retries": 0,
            "fragment_retries": 0,
            # A playlist only needs enough metadata to identify the URL.
            "playlist_items": "1",
            "extract_flat": "in_playlist",
        }
        with module.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        if not isinstance(info, dict):
            return ""

        candidates = [
            info.get("title"),
            info.get("playlist_title"),
            info.get("fulltitle"),
            info.get("description"),
        ]
        entries = info.get("entries")
        if isinstance(entries, (list, tuple)) and entries:
            first = entries[0]
            if isinstance(first, dict):
                candidates.extend((first.get("title"), first.get("fulltitle")))

        for value in candidates:
            title = GleanDownloader._normalize_history_title(value, limit=180)
            if title:
                return title
    except Exception:
        return ""
    return ""


def _get_gallery_runtime():
    global gallery_config, gallery_exception, _GalleryMediaJob
    if gallery_config is not None and _GalleryMediaJob is not None:
        return gallery_config, gallery_exception, _GalleryMediaJob

    with _RUNTIME_IMPORT_LOCK:
        if gallery_config is not None and _GalleryMediaJob is not None:
            return gallery_config, gallery_exception, _GalleryMediaJob
        try:
            from gallery_dl import config as runtime_config
            from gallery_dl import exception as runtime_exception
            from gallery_dl.job import DownloadJob as GalleryDownloadJob
        except ImportError as exc:
            raise RuntimeError(tr("error.gallerydl_missing")) from exc

        class GalleryMediaJob(GalleryDownloadJob):  # type: ignore[misc, valid-type]
            """Single-post gallery job with cancellation and media filtering."""

            def __init__(self, url, parent=None, cancel_event=None, progress_callback=None, media_mode="images"):
                self._glean_cancel_event = cancel_event or getattr(parent, "_glean_cancel_event", None)
                self._glean_progress_callback = progress_callback or getattr(parent, "_glean_progress_callback", None)
                self._glean_media_mode = getattr(parent, "_glean_media_mode", media_mode)
                self._glean_stats = getattr(parent, "_glean_stats", {"images": 0, "videos": 0, "title": ""})
                super().__init__(url, parent)

            def _check_glean_cancelled(self) -> None:
                if self._glean_cancel_event is not None and self._glean_cancel_event.is_set():
                    raise runtime_exception.TerminateExtraction()

            def handle_url(self, url, kwdict):
                self._check_glean_cancelled()
                if not self._glean_stats.get("title"):
                    for key in ("title", "caption", "description", "content", "text"):
                        value = " ".join(str(kwdict.get(key) or "").split()).strip()
                        if value:
                            self._glean_stats["title"] = value[:180]
                            break
                extension = str(kwdict.get("extension") or "").lower().lstrip(".")
                if not extension:
                    extension = _extension_from_url(url)
                media_kind = "images" if extension in _IMAGE_EXTENSIONS else "videos" if extension in _VIDEO_EXTENSIONS else ""
                if not media_kind or (self._glean_media_mode == "images" and media_kind != "images"):
                    return
                super().handle_url(url, kwdict)
                final_path = getattr(self.pathfmt, "path", None) if self.pathfmt is not None else None
                if final_path and os.path.isfile(final_path):
                    self._glean_stats[media_kind] = int(self._glean_stats.get(media_kind, 0)) + 1
                    if self._glean_progress_callback:
                        total = int(self._glean_stats.get("images", 0)) + int(self._glean_stats.get("videos", 0))
                        self._glean_progress_callback({"status": "gallery", "gallery_item": total})

            def handle_queue(self, url, kwdict):
                self._check_glean_cancelled()
                return super().handle_queue(url, kwdict)

        gallery_config = runtime_config
        gallery_exception = runtime_exception
        _GalleryMediaJob = GalleryMediaJob
    return gallery_config, gallery_exception, _GalleryMediaJob


class GleanDownloader:
    """Glean download engine for supported media sources.

    yt-dlp handles video/audio and YouTube playlists. gallery-dl is used for
    image media inside individual Instagram/TikTok/X posts. Social profile,
    timeline and account-wide URLs are rejected by strict URL validation.
    """

    SOCIAL_PLATFORMS = {"ig", "tt", "x"}

    def __init__(
        self,
        download_dir: str,
        ffmpeg_path: str,
        callback_progress: Optional[ProgressCallback] = None,
        message_callback: Optional[MessageCallback] = None,
    ) -> None:
        self.download_dir = os.path.abspath(download_dir)
        self.ffmpeg_path = os.path.abspath(ffmpeg_path)
        self.progress_callback = callback_progress
        self.message_callback = message_callback
        self._last_progress_emit = 0.0
        self._last_progress_value = -1.0
        self.cancel_event = threading.Event()
        self._playlist_mode = False
        self._aggregate_mode = False
        self._last_announced_title = ""
        self._last_playlist_title = ""
        self.last_title = ""
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)

    def set_download_dir(self, path: str) -> None:
        self.download_dir = os.path.abspath(path)
        Path(self.download_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def classify_url(cls, url: str) -> str | None:
        return classify_url(url)

    def validate_url(self, url: str) -> str:
        platform = self.classify_url(url)
        if platform:
            return platform
        message = tr("error.invalid_url")
        self._message(message)
        raise ValueError(message)

    def error_audio(self) -> None:
        message = tr("error.audio_unsupported")
        self._message(message)
        raise ValueError(message)

    def is_playlist(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            return "/playlist" in parsed.path.lower() or bool(query.get("list"))
        except Exception:
            return "playlist" in url.lower() or "list=" in url.lower()

    @staticmethod
    def _video_format(platform: str, quality: str) -> str:
        if platform != "yt":
            # Do not treat audio-only slideshow tracks as a video download.
            # Image slideshows/carousels are handled by gallery-dl instead.
            return "bestvideo+bestaudio/best[vcodec!=none]/bestvideo"
        if quality == "Baixa":
            return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"
        if quality == "Média":
            return "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        return "bestvideo+bestaudio/best"

    @staticmethod
    def _audio_format(quality: str) -> str:
        if quality == "Baixa":
            return "worstaudio/worst"
        if quality == "Média":
            return "bestaudio[abr<=128]/bestaudio/best"
        return "bestaudio/best"

    @staticmethod
    def _audio_bitrate(quality: str) -> str:
        if quality == "Baixa":
            return "96"
        if quality == "Média":
            return "128"
        return "320"

    def _ensure_runtime(self):
        module = _get_ytdlp()
        if not os.path.isfile(self.ffmpeg_path):
            raise FileNotFoundError(tr("error.ffmpeg_missing", path=self.ffmpeg_path))
        return module

    def _check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise DownloadCancelled(tr("download.cancelled"))

    def _message(self, message: str) -> None:
        if self.message_callback:
            self.message_callback(message)

    def _progress(self, payload: dict) -> None:
        if self.progress_callback:
            self.progress_callback(payload)

    @staticmethod
    def _normalize_history_title(value: object, limit: int = 180) -> str:
        title = " ".join(str(value or "").split()).strip()
        if len(title) > limit:
            title = title[: limit - 1].rstrip() + "…"
        return title

    def _remember_history_title(self, value: object) -> None:
        title = self._normalize_history_title(value)
        if title:
            self.last_title = title

    def _announce_info(self, info: dict) -> None:
        playlist_title = str(info.get("playlist_title") or info.get("playlist") or "").strip()
        if self._playlist_mode and playlist_title and playlist_title != self._last_playlist_title:
            self._last_playlist_title = playlist_title
            self._remember_history_title(playlist_title)
            self._message(tr("download.playlist_title", title=playlist_title))

        title = str(info.get("title") or "").strip()
        if title and title != self._last_announced_title:
            self._last_announced_title = title
            if not self._playlist_mode:
                self._remember_history_title(title)
            self._message(tr("download.item_title", title=title))

    def history_title(self, url: str) -> str:
        if self.last_title:
            return self.last_title
        try:
            parsed = urlparse(url)
            parts = [part for part in parsed.path.split("/") if part]
        except Exception:
            parts = []
        platform = self.classify_url(url)
        if platform == "ig" and len(parts) >= 2:
            return f"Instagram post {parts[1]}"
        if platform == "x" and len(parts) >= 3:
            return f"X post {parts[-1]}"
        if platform == "tt" and parts:
            return f"TikTok video {parts[-1]}"
        if platform == "yt":
            return "YouTube"
        return "Download"

    @staticmethod
    def _safe_int(value, default: int = 1) -> int:
        try:
            return int(value or default)
        except (TypeError, ValueError):
            return default

    def _progress_hook(self, data: dict) -> None:
        self._check_cancelled()
        status = data.get("status")
        info = data.get("info_dict") or {}
        self._announce_info(info)

        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            downloaded = data.get("downloaded_bytes") or 0
            item_progress = (downloaded / total) if total else 0.0
            speed = data.get("speed") or 0
            eta = data.get("eta")

            current_item = self._safe_int(info.get("playlist_index"), 1)
            total_items = self._safe_int(info.get("n_entries") or info.get("playlist_count"), 1)
            total_items = max(total_items, 1)
            current_item = max(1, min(current_item, total_items))
            overall = ((current_item - 1) + item_progress) / total_items if self._aggregate_mode else item_progress

            speed_text = f"{speed / 1024:.2f} KB/s" if speed else tr("status.calculating")
            if eta is None:
                time_text = tr("status.calculating")
            else:
                eta = max(0, int(eta))
                time_text = tr("status.time_remaining", minutes=eta // 60, seconds=eta % 60)

            overall = max(0.0, min(overall, 1.0))
            now = time.monotonic()
            # yt-dlp can invoke progress hooks dozens of times per second.
            # Throttling UI signals avoids needless Qt event traffic while
            # keeping the progress display fluid.
            if (now - self._last_progress_emit >= 0.08
                    or abs(overall - self._last_progress_value) >= 0.01
                    or overall >= 1.0):
                self._last_progress_emit = now
                self._last_progress_value = overall
                self._progress({
                    "status": "downloading",
                    "progress": overall,
                    "current_item": current_item,
                    "total_items": total_items,
                    "speed": speed_text,
                    "time": time_text,
                })
        elif status == "finished":
            current_item = self._safe_int(info.get("playlist_index"), 1)
            total_items = max(self._safe_int(info.get("n_entries") or info.get("playlist_count"), 1), 1)
            self._progress({
                "status": "finished",
                "progress": current_item / total_items if self._aggregate_mode else 1.0,
                "current_item": current_item,
                "total_items": total_items,
            })

    def _postprocessor_hook(self, data: dict) -> None:
        self._check_cancelled()
        if data.get("status") == "started":
            self._progress({"status": "processing"})
            self._message(tr("download.finalizing"))

    def _base_options(self) -> dict:
        return {
            "ffmpeg_location": self.ffmpeg_path,
            "windowsfilenames": True,
            "continuedl": True,
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            # Multiple HLS/DASH fragments can download in parallel. Four is a
            # conservative desktop default that improves throughput without
            # creating an excessive number of connections.
            "concurrent_fragment_downloads": 4,
        }

    def _build_options(self, url: str, media_type: str, quality: str, thumbnails: bool) -> dict:
        platform = self.validate_url(url)
        is_playlist = platform == "yt" and self.is_playlist(url)
        is_social = platform in self.SOCIAL_PLATFORMS
        self._playlist_mode = is_playlist
        self._aggregate_mode = is_playlist or is_social

        if media_type == "audio" and platform != "yt":
            self.error_audio()
        if media_type not in {"audio", "video"}:
            raise ValueError(tr("error.invalid_media_type"))

        opts = self._base_options()
        # Social extractors can represent one post as multiple entries. Keep
        # playlist processing enabled so multi-video X/Instagram posts are not
        # truncated to a single media item.
        opts["noplaylist"] = not (is_playlist or is_social)
        if is_social:
            # A carousel may contain image-only entries that yt-dlp cannot
            # download. gallery-dl handles those; yt-dlp should continue to any
            # video entries instead of aborting the whole post.
            opts["ignoreerrors"] = True

        if is_playlist:
            outtmpl = os.path.join(self.download_dir, "%(playlist_title)s", "%(title)s.%(ext)s")
        elif platform == "yt":
            outtmpl = os.path.join(self.download_dir, "%(title)s.%(ext)s")
        else:
            outtmpl = os.path.join(self.download_dir, "%(uploader)s-%(upload_date)s-%(id)s-%(title).120B.%(ext)s")
        opts["outtmpl"] = outtmpl

        postprocessors: list[dict] = []
        if media_type == "audio":
            opts["format"] = self._audio_format(quality)
            postprocessors.append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": self._audio_bitrate(quality),
            })
        else:
            opts["format"] = self._video_format(platform, quality)
            opts["merge_output_format"] = "mp4"

        if thumbnails:
            opts["writethumbnail"] = True
            opts["convertthumbnails"] = "png"
            postprocessors.append({"key": "FFmpegThumbnailsConvertor", "format": "png"})

        if postprocessors:
            opts["postprocessors"] = postprocessors
        return opts

    @classmethod
    def _count_video_entries(cls, info) -> int:
        if not isinstance(info, dict):
            return 0
        entries = info.get("entries")
        if entries is not None:
            try:
                return sum(cls._count_video_entries(entry) for entry in entries if entry)
            except TypeError:
                return 0

        candidates = []
        requested = info.get("requested_downloads") or info.get("requested_formats")
        if isinstance(requested, list):
            candidates.extend(item for item in requested if isinstance(item, dict))
        candidates.append(info)

        for item in candidates:
            vcodec = str(item.get("vcodec") or "").lower()
            # An explicitly audio-only entry must never be counted as video,
            # even if its container happens to use a video-capable extension.
            if vcodec == "none":
                continue
            if vcodec:
                return 1
            ext = str(item.get("ext") or "").lower()
            if ext in _VIDEO_EXTENSIONS:
                return 1
        return 0

    def _run_ytdlp(
        self,
        url: str,
        media_type: str,
        quality: str,
        thumbnails: bool,
        output_dir: str | None = None,
        emit_final: bool = True,
    ) -> int:
        ytdlp = self._ensure_runtime()
        self._last_announced_title = ""
        self._last_playlist_title = ""
        self._last_progress_emit = 0.0
        self._last_progress_value = -1.0

        original_dir = self.download_dir
        if output_dir:
            self.set_download_dir(output_dir)
        try:
            opts = self._build_options(url.strip(), media_type, quality, thumbnails)
            self._check_cancelled()
            with ytdlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url.strip(), download=True)
            self._check_cancelled()
            if isinstance(info, dict):
                if self._playlist_mode:
                    self._remember_history_title(info.get("title") or info.get("playlist_title") or self._last_playlist_title)
                else:
                    self._remember_history_title(info.get("title") or self._last_announced_title)
            count = self._count_video_entries(info) if media_type == "video" else (1 if info else 0)
            if emit_final:
                self._progress({"status": "finalized", "progress": 1.0})
                self._message(tr("download.finalized"))
            return count
        finally:
            self.download_dir = original_dir

    @staticmethod
    def _load_gallery_config_once() -> None:
        global _GALLERY_CONFIG_LOADED
        if _GALLERY_CONFIG_LOADED:
            return
        with _GALLERY_CONFIG_LOCK:
            if not _GALLERY_CONFIG_LOADED:
                # Glean intentionally does not load external authentication or
                # browser-session configuration.
                _GALLERY_CONFIG_LOADED = True

    def _download_social_gallery(self, url: str, media_mode: str = "images") -> tuple[int, int]:
        runtime_config, runtime_exception, job_class = _get_gallery_runtime()
        self._check_cancelled()
        self._load_gallery_config_once()
        self._progress({"status": "gallery", "gallery_item": 0})
        self._message(tr("download.gallery_scanning"))

        with _GALLERY_CONFIG_LOCK:
            runtime_config.set(("extractor",), "base-directory", self.download_dir)
            runtime_config.set(("extractor",), "path-restrict", "windows")
            for category in ("instagram", "tiktok", "twitter"):
                runtime_config.set(("extractor", category), "base-directory", self.download_dir)
                runtime_config.set(("extractor", category), "path-restrict", "windows")

            self.validate_url(url)

            # Single-post media defaults. URL validation prevents account-wide
            # extractors from ever being reached.
            runtime_config.set(("extractor", "instagram"), "videos", True)
            runtime_config.set(("extractor", "instagram"), "order-files", "asc")
            runtime_config.set(("extractor", "twitter"), "videos", True)
            runtime_config.set(("extractor", "tiktok"), "photos", True)
            runtime_config.set(("extractor", "tiktok"), "videos", True)
            runtime_config.set(("extractor", "tiktok"), "audio", False)
            runtime_config.set(("extractor", "tiktok"), "covers", False)

            try:
                job = job_class(
                    url.strip(),
                    cancel_event=self.cancel_event,
                    progress_callback=self._progress,
                    media_mode=media_mode,
                )
                status = job.run()
            except BaseException as exc:
                if self.cancel_event.is_set():
                    raise DownloadCancelled(tr("download.cancelled")) from exc
                if isinstance(exc, runtime_exception.TerminateExtraction):
                    raise DownloadCancelled(tr("download.cancelled")) from exc
                raise

        image_count = int(job._glean_stats.get("images", 0))
        video_count = int(job._glean_stats.get("videos", 0))
        self._remember_history_title(job._glean_stats.get("title", ""))
        if status and image_count == 0 and video_count == 0:
            raise RuntimeError(tr("error.gallery_download_failed"))
        return image_count, video_count

    def _run_social_video(self, url: str, quality: str, thumbnails: bool) -> None:
        self.validate_url(url)
        self.last_title = ""
        image_count = 0
        video_count = 0
        gallery_error: Exception | None = None
        video_error: Exception | None = None

        try:
            image_count, _ignored_videos = self._download_social_gallery(url, media_mode="images")
        except DownloadCancelled:
            raise
        except Exception as exc:
            gallery_error = exc

        self._check_cancelled()
        try:
            video_count = self._run_ytdlp(
                url,
                "video",
                quality,
                thumbnails,
                emit_final=False,
            )
        except DownloadCancelled:
            raise
        except Exception as exc:
            video_error = exc

        self._check_cancelled()
        if image_count or video_count:
            self._progress({"status": "finalized", "progress": 1.0})
            self._message(tr(
                "download.social_finalized",
                images=image_count,
                videos=video_count,
            ))
            return

        if gallery_error is not None:
            if video_error is not None:
                raise RuntimeError(
                    tr("error.social_download_failed", gallery=gallery_error, video=video_error)
                ) from video_error
            raise gallery_error
        if video_error is not None:
            raise video_error
        raise RuntimeError(tr("error.no_media_found"))

    def download_audio(self, url: str, quality: str, thumbnails: bool, output_dir: str | None = None) -> None:
        if self.validate_url(url) != "yt":
            self.error_audio()
        self._run_ytdlp(url, "audio", quality, thumbnails, output_dir)

    def download_video(self, url: str, quality: str, thumbnails: bool, output_dir: str | None = None) -> None:
        platform = self.validate_url(url)
        if output_dir:
            original_dir = self.download_dir
            self.set_download_dir(output_dir)
            try:
                if platform in self.SOCIAL_PLATFORMS:
                    self._run_social_video(url, quality, thumbnails)
                else:
                    self._run_ytdlp(url, "video", quality, thumbnails)
            finally:
                self.download_dir = original_dir
            return
        if platform in self.SOCIAL_PLATFORMS:
            self._run_social_video(url, quality, thumbnails)
        else:
            self._run_ytdlp(url, "video", quality, thumbnails)

    def download_playlist(self, url: str, quality: str, thumbnails: bool, media_type: str) -> None:
        if self.validate_url(url) != "yt":
            raise ValueError(tr("error.playlist_youtube_only"))
        self._run_ytdlp(url, media_type, quality, thumbnails)

    def _get_playlist_title(self, url: str) -> str:
        ytdlp = self._ensure_runtime()
        with ytdlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        return str((info or {}).get("title") or tr("download.playlist_default"))

    def download(self, url: str, media_type: str, quality: str, thumbnails: bool) -> None:
        self.last_title = ""
        platform = self.validate_url(url)
        if platform == "yt" and self.is_playlist(url):
            self.download_playlist(url, quality, thumbnails, media_type)
        elif media_type == "audio":
            self.download_audio(url, quality, thumbnails)
        elif media_type == "video":
            self.download_video(url, quality, thumbnails)
        else:
            raise ValueError(tr("error.invalid_media_type"))
