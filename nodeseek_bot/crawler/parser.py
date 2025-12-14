from __future__ import annotations

import re

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
