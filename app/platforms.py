from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

_INSTAGRAM_POST = re.compile(r"^/p/[A-Za-z0-9_-]+/?$", re.I)
_X_STATUS = re.compile(r"^/[A-Za-z0-9_]+/status/\d+(?:/photo/[1-9]\d*)?/?$", re.I)
_TIKTOK_VIDEO = re.compile(r"^/[^/]+/video/\d+/?$", re.I)
_YOUTUBE_ID_PATH = re.compile(r"^/(?:shorts|live|embed)/[^/]+/?$", re.I)


def classify_url(url: str) -> str | None:
    """Classify only URL shapes Glean is intentionally allowed to download.

    Social platforms are restricted to one explicit post/video URL. Profile,
    timeline, search and unsupported short-link URLs are rejected. YouTube keeps
    support for individual videos and playlists, but not channel-wide URLs.
    """
    value = str(url or "").strip()
    if not value:
        return None
    try:
        parsed = urlparse(value)
        if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
            return None
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

    if host in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
        if facebook_media_kind(value):
            return "fb"

    return None


def facebook_media_kind(url: str) -> str | None:
    """Recognize explicit Facebook media, never profiles or entire albums."""
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme != "https" or parsed.hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}:
            return None
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query, keep_blank_values=True)
        def numeric(key):
            values = query.get(key, [])
            return len(values) == 1 and bool(re.fullmatch(r"[0-9]+", values[0]))
        if path in {"/photo", "/photo.php"} and numeric("fbid"):
            return "image"
        if re.fullmatch(r"/reel/[0-9]+", path) or re.fullmatch(r"/[^/]+/videos/[0-9]+", path):
            return "video"
        if path in {"/watch", "/video.php", "/video/video.php"} and numeric("v"):
            return "video"
    except (ValueError, AttributeError):
        pass
    return None


def facebook_photo_url(url: str) -> str:
    """Drop album context so gallery-dl receives only the requested photo."""
    if facebook_media_kind(url) != "image":
        raise ValueError("Not a Facebook photo URL")
    return "https://www.facebook.com/photo/?" + urlencode({"fbid": parse_qs(urlparse(url).query)["fbid"][0]})


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



def youtube_single_video_url(url: str) -> str | None:
    """Extract the individual video from a YouTube video+playlist URL.

    Only return a choice when the URL contains both a playable media ID and
    an explicit playlist parameter. In particular, /playlist?list=... has no
    individual video to offer as an alternative.
    """
    if not is_youtube_playlist(url):
        return None
    parsed = urlparse(str(url or "").strip())
    query = parse_qs(parsed.query)
    if parsed.path.lower() == "/watch":
        video_id = next((v for v in query.get("v", []) if v), "")
        if not video_id:
            return None
        # Keep exactly the v parameter, regardless of query-string ordering.
        return urlunparse(parsed._replace(query=urlencode({"v": video_id}), fragment=""))
    if (parsed.hostname or "").lower() == "youtu.be" or _YOUTUBE_ID_PATH.match(parsed.path):
        # Short links, Shorts and Live URLs already identify a single video.
        return urlunparse(parsed._replace(query="", fragment=""))
    return None


def social_image_target(url: str) -> tuple[str, str] | None:
    """Return canonical post URL and requested one-based image position."""
    platform = classify_url(url)
    if platform not in {"ig", "x"}:
        return None
    parsed = urlparse(url.strip())
    if platform == "ig":
        values = parse_qs(parsed.query).get("img_index", [])
        if len(values) != 1 or not re.fullmatch(r"[1-9][0-9]*", values[0]):
            return None
        index = values[0]
        path = parsed.path
    else:
        match = re.search(r"/photo/([1-9][0-9]*)/?$", parsed.path)
        if not match:
            return None
        index = match[1]
        path = parsed.path[:match.start()]
    return urlunparse(parsed._replace(path=path, query="", fragment="")), index


def media_kind(url: str) -> str:
    """URL-level classification; ambiguous posts need metadata or user choice."""
    platform = classify_url(url)
    if platform == 'fb': return facebook_media_kind(url) or 'unknown'
    if platform == 'yt' or platform == 'tt': return 'video'
    if platform in ('ig', 'x') and social_image_target(url): return 'image'
    return 'unknown'
