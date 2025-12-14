from __future__ import annotations

import logging

from telegram.ext import Application


logger = logging.getLogger(__name__)


async def maybe_send_consecutive_failure_alert(
    application: Application,
    alert_chat_id: int,
    name: str,
    count: int,
    threshold: int,
) -> None:
    if threshold <= 0:
        return
    if count != threshold:
        # only alert on the edge to avoid spamming
        return

    text = f"告警：{name} 连续失败达到 {count} 次（阈值 {threshold}）。已自动降级/退避，请检查日志与 Cookie/AI 服务。"
    try:
        await application.bot.send_message(chat_id=alert_chat_id, text=text)
    except Exception:
        logger.exception("failed to send alert")
