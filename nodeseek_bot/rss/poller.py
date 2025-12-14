from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser

from nodeseek_bot.storage.types import FeedItem
from nodeseek_bot.utils import collapse_ws


logger = logging.getLogger(__name__)


def _to_dt(entry: dict) -> datetime | None:
    # feedparser typically provides time_struct as entry.published_parsed / updated_parsed
    ts = entry.get("published_parsed") or entry.get("updated_parsed")
    if not ts:
        return None
    return datetime(*ts[:6], tzinfo=timezone.utc)


class RssPoller:
    def __init__(self, rss_url: str):
        self._url = rss_url

    def poll(self) -> list[FeedItem]:
        feed = feedparser.parse(self._url)
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
