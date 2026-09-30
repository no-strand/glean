from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from .i18n import DEFAULT_LOCALE
from .version import APP_NAME


@dataclass(slots=True)
class AppConfig:
    video_quality: str = "Alta"
    audio_quality: str = "Alta"
    download_path: str = ""
    download_thumbnails: bool = False
    language: str = DEFAULT_LOCALE
    start_with_windows: bool = False


class ConfigStore:
    SECTION = "Settings"
    VALID_QUALITIES = {"Alta", "Média", "Baixa"}
    CONFIG_SCHEMA_VERSION = 1
    CONFIG_SCHEMA_KEY = "ConfigSchema"

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        if path is None:
            local_appdata = os.getenv("LOCALAPPDATA")
            base = Path(local_appdata) if local_appdata else Path.home() / ".config"
            path = base / APP_NAME / "config.ini"
        self.path = Path(path)

    @staticmethod
    def default_download_path() -> str:
        userprofile = os.getenv("USERPROFILE")
        base = Path(userprofile) if userprofile else Path.home()
        return str((base / "Downloads" / APP_NAME).resolve())

    @staticmethod
    def _bool_value(raw: object, default: bool = False) -> bool:
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "sim"}

    def load(self) -> AppConfig:
        parser = configparser.ConfigParser()
        if self.path.exists():
            try:
                parser.read(self.path, encoding="utf-8")
            except (OSError, configparser.Error):
                parser = configparser.ConfigParser()

        section = parser[self.SECTION] if parser.has_section(self.SECTION) else {}
        video_quality = str(section.get("Video_Quality", "Alta"))
        audio_quality = str(section.get("Audio_Quality", "Alta"))
        if video_quality not in self.VALID_QUALITIES:
            video_quality = "Alta"
        if audio_quality not in self.VALID_QUALITIES:
            audio_quality = "Alta"

        try:
            config_schema = int(str(section.get(self.CONFIG_SCHEMA_KEY, "0") or "0"))
        except (TypeError, ValueError):
            config_schema = 0

        # Configurations written before the current Glean schema can contain a
        # stale default download directory. Reset only once during migration;
        # after this schema is saved, any folder selected by the user remains
        # persistent normally.
        needs_path_migration = self.path.exists() and config_schema < self.CONFIG_SCHEMA_VERSION
        if needs_path_migration:
            download_path = self.default_download_path()
        else:
            download_path = str(section.get("DownloadPath", "")).strip() or self.default_download_path()

        thumbnails = self._bool_value(section.get("Download_Thumbnails", False), False)
        language = str(section.get("Language", DEFAULT_LOCALE) or DEFAULT_LOCALE)
        start_with_windows = self._bool_value(section.get("Start_With_Windows", False), False)
        config = AppConfig(
            video_quality=video_quality,
            audio_quality=audio_quality,
            download_path=os.path.abspath(os.path.expanduser(download_path)),
            download_thumbnails=thumbnails,
            language=language,
            start_with_windows=start_with_windows,
        )
        if needs_path_migration:
            try:
                self.save(config)
            except OSError:
                # A read-only config must not prevent Glean from starting; the
                # in-memory path is still corrected for the current session.
                pass
        return config

    def save(self, config: AppConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        parser = configparser.ConfigParser()
        parser[self.SECTION] = {
            self.CONFIG_SCHEMA_KEY: str(self.CONFIG_SCHEMA_VERSION),
            "Video_Quality": config.video_quality,
            "Audio_Quality": config.audio_quality,
            "DownloadPath": os.path.abspath(config.download_path),
            "Download_Thumbnails": "true" if config.download_thumbnails else "false",
            "Language": config.language,
            "Start_With_Windows": "true" if config.start_with_windows else "false",
        }
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            parser.write(handle)
        tmp.replace(self.path)
