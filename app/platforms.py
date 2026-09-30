from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_INSTAGRAM_POST = re.compile(r"^/p/[A-Za-z0-9_-]+/?$", re.I)
_X_STATUS = re.compile(r"^/[A-Za-z0-9_]+/status/\d+/?$", re.I)
_TIKTOK_VIDEO = re.compile(r"^/[^/]+/video/\d+/?$", re.I)
_YOUTUBE_ID_PATH = re.compile(r"^/(?:shorts|live|embed)/[^/]+/?$", re.I)


def classify_url(url: str) -> str | None:
    """Classify only URL shapes Glean is intentionally allowed to download.

    Social platforms are restricted to one explicit post/video URL. Profile,
    timeline, search, reel and short-link URLs are rejected. YouTube keeps
    support for individual videos and playlists, but not channel-wide URLs.
    """
    value = str(url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
    except Exception:
        return None

    if parsed.scheme.lower() != "https":
        return None

    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    query = parse_qs(parsed.query)

    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if path == "/watch" and query.get("v"):
            return "yt"
        if path == "/playlist" and query.get("list"):
            return "yt"
        if _YOUTUBE_ID_PATH.match(path):
            return "yt"
        return None

    if host == "youtu.be":
        parts = [part for part in path.split("/") if part]
        return "yt" if len(parts) == 1 else None

    if host == "www.instagram.com" and _INSTAGRAM_POST.match(path):
        return "ig"

    if host == "x.com" and _X_STATUS.match(path):
        return "x"

    if host == "www.tiktok.com" and _TIKTOK_VIDEO.match(path):
        return "tt"

    return None

def is_youtube_playlist(url: str) -> bool:
    """Return True when a supported YouTube URL explicitly targets a playlist."""
    if classify_url(url) != "yt":
        return False
    try:
        parsed = urlparse(str(url or "").strip())
        query = parse_qs(parsed.query)
        return parsed.path.lower() == "/playlist" or bool(query.get("list"))
    except Exception:
        return False

