from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from .version import APP_NAME


class HistoryStore:
    """Small, lazy, atomic download history.

    Entries are unique by URL. Re-downloading a URL moves it to the top with a
    fresh title and timestamp. Older history files without titles remain valid.
    """

    MAX_ENTRIES = 500

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        if path is None:
            local_appdata = os.getenv("LOCALAPPDATA")
            base = Path(local_appdata) if local_appdata else Path.home() / ".config"
            path = base / APP_NAME / "history.json"
        self.path = Path(path)
        self._lock = RLock()
        self._entries: list[dict[str, str]] | None = None
        self._dirty = False

    def _ensure_loaded(self) -> None:
        if self._entries is not None:
            return
        entries: list[dict[str, str]] = []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else []
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "").strip()
                    title = str(item.get("title") or "").strip()
                    timestamp = str(item.get("timestamp") or "").strip()
                    if url:
                        entries.append({"url": url, "title": title, "timestamp": timestamp})
        except (OSError, ValueError, json.JSONDecodeError):
            entries = []
        self._entries = entries[: self.MAX_ENTRIES]

    def entries(self) -> list[dict[str, str]]:
        with self._lock:
            self._ensure_loaded()
            return [dict(item) for item in (self._entries or [])]

    def add(self, url: str, title: str = "") -> None:
        url = str(url or "").strip()
        title = " ".join(str(title or "").split()).strip()
        if not url:
            return
        with self._lock:
            self._ensure_loaded()
            assert self._entries is not None
            self._entries = [item for item in self._entries if item.get("url") != url]
            stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            self._entries.insert(0, {"url": url, "title": title, "timestamp": stamp})
            del self._entries[self.MAX_ENTRIES :]
            self._dirty = True

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            self._ensure_loaded()
            if not self._dirty:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            payload = json.dumps(self._entries or [], ensure_ascii=False, separators=(",", ":"))
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.path)
            self._dirty = False
