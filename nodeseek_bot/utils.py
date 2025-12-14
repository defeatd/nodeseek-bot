from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timezone


_UTM_PREFIXES = ("utm_",)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def canonicalize_url(url: str) -> str:
    url = url.strip()
    parsed = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.startswith(_UTM_PREFIXES)]
    cleaned = parsed._replace(fragment="", query=urlencode(query))
    return urlunparse(cleaned)


def collapse_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"
