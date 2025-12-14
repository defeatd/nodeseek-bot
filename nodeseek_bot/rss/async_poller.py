from __future__ import annotations

import logging

import feedparser
import httpx

from nodeseek_bot.storage.types import FeedItem
from nodeseek_bot.rss.poller import _to_dt
from nodeseek_bot.utils import collapse_ws


logger = logging.getLogger(__name__)


class AsyncRssPoller:
    def __init__(self, rss_url: str, timeout_seconds: int = 20) -> None:
        self._url = rss_url
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def poll(self) -> list[FeedItem]:
        resp = await self._client.get(self._url)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        if getattr(feed, "bozo", 0):
            logger.warning("rss parse bozo=%s error=%s", feed.bozo, getattr(feed, "bozo_exception", None))

        items: list[FeedItem] = []
        for entry in feed.entries:
            url = entry.get("link")
            title = entry.get("title")
            if not url or not title:
                continue
            guid = entry.get("id") or entry.get("guid")
            published_at = _to_dt(entry)
            summary = entry.get("summary") or entry.get("description") or ""
            items.append(
                FeedItem(
                    guid=str(guid) if guid else None,
                    url=str(url),
                    title=collapse_ws(str(title)),
                    published_at=published_at,
                    summary=collapse_ws(str(summary)),
                )
            )
        return items
