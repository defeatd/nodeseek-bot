from __future__ import annotations

import html

from nodeseek_bot.storage.types import PostRow, ScoreResult, SummaryResult
from nodeseek_bot.utils import truncate


def _escape_text(s: str) -> str:
    return html.escape(s or "")


def _escape_url_raw(url: str) -> str:
    """Escape URL for TG HTML parse_mode while keeping it visually 'raw'.

    Use HTML entities so the displayed text remains the original URL.
    """
    return html.escape(url or "", quote=False)


def render_message(post: PostRow, summary: SummaryResult | None, score: ScoreResult, max_chars: int = 3800) -> str:
    title = _escape_text(post.title)
    url_raw = _escape_url_raw(post.url)

    lines: list[str] = [f"<b>{title}</b>", f"打开原帖：{url_raw}"]

    if summary is not None and summary.summary_text:
        lines.append("<b>摘要</b>")
        lines.append(_escape_text(summary.summary_text))

    if summary is not None and summary.key_points:
        lines.append("<b>要点</b>")
        for p in summary.key_points[:6]:
            lines.append(f"- {_escape_text(p)}")

    if summary is not None and summary.image_summaries:
        lines.append("<b>图片识别</b>")
        for p in summary.image_summaries[:10]:
            lines.append(f"- {_escape_text(p)}")

    text = "\n".join(lines)
    return truncate(text, max_chars)
