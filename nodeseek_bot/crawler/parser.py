from __future__ import annotations

import re
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from nodeseek_bot.utils import collapse_ws


_LOGIN_HINTS = [
    "登录",
    "需要登录",
    "请登录",
    "需要权限",
]

_ANTIBOT_HINTS = [
    "cf_clearance",
    "Cloudflare",
    "Just a moment",
    "captcha",
    "challenge",
]


_MD_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?P<url>[^\)\s]+)(?:\s+\"[^\"]*\")?\)",
    flags=re.IGNORECASE,
)


def extract_image_urls_from_markdown(markdown: str, base_url: str = "") -> list[str]:
    urls: list[str] = []
    if not markdown:
        return urls

    for m in _MD_IMAGE_RE.finditer(markdown):
        u = (m.group("url") or "").strip()
        if not u:
            continue
        if base_url:
            u = urljoin(base_url, u)
        urls.append(u)

    # Also handle inline HTML <img> within markdown.
    urls.extend(extract_image_urls_from_html(markdown, base_url=base_url))

    # Dedup keep order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_urls_from_srcset(srcset: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for part in (srcset or "").split(","):
        item = part.strip()
        if not item:
            continue
        # "url 2x" or "url 480w"
        u = item.split()[0].strip()
        if not u:
            continue
        if base_url:
            u = urljoin(base_url, u)
        urls.append(u)
    return urls


def extract_image_urls_from_html(html: str, base_url: str = "") -> list[str]:
    if not html:
        return []

    tree = HTMLParser(html)

    urls: list[str] = []
    for img in tree.css("img"):
        src = (img.attributes.get("src") or "").strip()
        data_src = (img.attributes.get("data-src") or "").strip()
        srcset = (img.attributes.get("srcset") or "").strip()

        candidates: list[str] = []
        if src:
            candidates.append(src)
        if data_src:
            candidates.append(data_src)
        if srcset:
            candidates.extend(_extract_urls_from_srcset(srcset, base_url))

        for u in candidates:
            if not u:
                continue
            if base_url:
                u = urljoin(base_url, u)
            urls.append(u)

    # Dedup keep order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def detect_antibot(html: str) -> bool:
    hay = html.lower()
    return any(h.lower() in hay for h in _ANTIBOT_HINTS)


def detect_login_required(html: str) -> bool:
    hay = html.lower()
    return any(h.lower() in hay for h in _LOGIN_HINTS)


def extract_main_text(html: str) -> str:
    tree = HTMLParser(html)

    # Remove some noise
    for node in tree.css("script, style, noscript"):
        node.decompose()

    # Try common containers first
    candidates = []
    for selector in [
        "article",
        ".post-content",
        ".topic-content",
        ".markdown-body",
        "main",
    ]:
        for n in tree.css(selector):
            text = n.text(separator="\n")
            text = collapse_ws(text)
            if len(text) >= 80:
                candidates.append(text)

    if candidates:
        return max(candidates, key=len)

    text = collapse_ws(tree.text(separator="\n"))
    return text
