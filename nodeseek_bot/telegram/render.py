from __future__ import annotations

import html

from nodeseek_bot.storage.types import PostRow, SummaryResult, ScoreResult
from nodeseek_bot.utils import truncate


def render_message(post: PostRow, summary: SummaryResult | None, score: ScoreResult, max_chars: int = 3800) -> str:
    title = html.escape(post.title)
    url = html.escape(post.url)

    lines: list[str] = []
    lines.append(f"<b>{title}</b>")
    lines.append(f"<a href=\"{url}\">打开原帖</a>")

    if summary is not None and summary.summary_text:
        lines.append("\n<b>摘要</b>")
        lines.append(html.escape(summary.summary_text))

    if summary is not None and summary.key_points:
        lines.append("\n<b>要点</b>")
        for p in summary.key_points[:6]:
            lines.append(f"- {html.escape(p)}")

    explain = score.explain or {}
    threshold = explain.get("threshold")
    conf = explain.get("confidence") or post.source_confidence

    lines.append("\n<b>信息量得分</b>")
    lines.append(f"{score.score_total:.1f} / 阈值 {threshold} / 置信度 {html.escape(str(conf))}")

    contribs = explain.get("contributions") or []
    if contribs:
        lines.append("\n<b>命中规则</b>")
        for c in contribs[:6]:
            name = html.escape(str(c.get("name")))
            sc = c.get("score")
            reason = html.escape(str(c.get("reason")))
            lines.append(f"- {name}: {sc} ({reason})")

    text = "\n".join(lines)
    return truncate(text, max_chars)
