from __future__ import annotations

import json
import sys
from pathlib import Path
from threading import RLock

DEFAULT_LOCALE = "en_US"


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


LOCALES_DIR = _project_root() / "locales"


class _Translator:
    def __init__(self) -> None:
        self._lock = RLock()
        self._locale = DEFAULT_LOCALE
        self._cache: dict[str, dict] = {}
        self._current_data: dict | None = None
        self._fallback_data: dict | None = None

    def _load(self, locale: str) -> dict:
        locale = str(locale or DEFAULT_LOCALE)
        with self._lock:
            cached = self._cache.get(locale)
            if cached is not None:
                return cached
            path = LOCALES_DIR / f"{locale}.json"
            try:
                with path.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("locale root must be an object")
            except (OSError, ValueError, json.JSONDecodeError):
                data = {}
            self._cache[locale] = data
            return data

    def available(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        try:
            paths = sorted(LOCALES_DIR.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            code = path.stem
            data = self._load(code)
            meta = data.get("_meta", {}) if isinstance(data, dict) else {}
            name = str(meta.get("name") or code)
            result.append((code, name))
        result.sort(key=lambda item: (item[0] != DEFAULT_LOCALE, item[1].casefold()))
        return result

    def has_locale(self, locale: str) -> bool:
        locale = str(locale or DEFAULT_LOCALE)
        if locale == DEFAULT_LOCALE:
            return True
        try:
            return (LOCALES_DIR / f"{locale}.json").is_file()
        except OSError:
            return False

    def set_locale(self, locale: str) -> str:
        locale = str(locale or DEFAULT_LOCALE)
        if not self.has_locale(locale):
            locale = DEFAULT_LOCALE
        current = self._load(locale)
        fallback = current if locale == DEFAULT_LOCALE else self._load(DEFAULT_LOCALE)
        self._locale = locale
        self._current_data = current
        self._fallback_data = fallback
        return locale

    def current(self) -> str:
        return self._locale

    def tr(self, key: str, **kwargs) -> str:
        key = str(key)
        current = self._current_data
        fallback = self._fallback_data
        if current is None or fallback is None:
            current = self._load(self._locale)
            fallback = current if self._locale == DEFAULT_LOCALE else self._load(DEFAULT_LOCALE)
            self._current_data = current
            self._fallback_data = fallback
        value = current.get(key, fallback.get(key, key))
        if not isinstance(value, str):
            value = str(value)
        if kwargs:
            try:
                value = value.format(**kwargs)
            except (KeyError, ValueError, IndexError):
                fallback_value = fallback.get(key, key)
                try:
                    value = str(fallback_value).format(**kwargs)
                except Exception:
                    value = str(fallback_value)
        return value


_TRANSLATOR = _Translator()


def tr(key: str, **kwargs) -> str:
    return _TRANSLATOR.tr(key, **kwargs)


def set_locale(locale: str) -> str:
    return _TRANSLATOR.set_locale(locale)


def current_locale() -> str:
    return _TRANSLATOR.current()


def available_locales() -> list[tuple[str, str]]:
    return _TRANSLATOR.available()
