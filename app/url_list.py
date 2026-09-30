from __future__ import annotations

import re
from pathlib import Path

from .platforms import classify_url

_SCHEME = re.compile(r"https?://", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    """Extract only valid, supported Glean URLs from free-form TXT content."""
    if not text:
        return []
    starts = [match.start() for match in _SCHEME.finditer(text)]
    if not starts:
        return []

    urls: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        chunk = text[start:end].strip()
        if index + 1 < len(starts):
            chunk = re.sub(r"[\s,;|<>\[\]{}()\"'`*^\\]+$", "", chunk)
        else:
            chunk = re.sub(r"[\s,;|<>\[\]{}()\"'`*~!^\\]+$", "", chunk)
        if re.search(r"\s", chunk):
            chunk = re.split(r"\s+", chunk, maxsplit=1)[0]
        if classify_url(chunk) is not None:
            urls.append(chunk)
    return urls


def load_url_file(path: str | Path) -> list[str]:
    file_path = Path(path)
    if file_path.suffix.lower() != ".txt":
        raise ValueError("Only .txt files are supported")
    text = file_path.read_text(encoding="utf-8-sig", errors="replace")
    return extract_urls(text)
