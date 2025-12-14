from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prometheus_client import Counter, Gauge, Histogram, start_http_server


logger = logging.getLogger(__name__)


@dataclass
class RuntimeStats:
    last_rss_poll_ts: float | None = None
    last_processed_post_id: int | None = None
    paused: bool = False
    fulltext_disabled: bool = False
    html_next_allowed_in_seconds: float | None = None
    consecutive_fetch_failures: int = 0
    consecutive_login_failures: int = 0
    consecutive_ai_failures: int = 0


class Metrics:
    def __init__(self) -> None:
        self.rss_polls_total = Counter("rss_polls_total", "Total RSS polls")
        self.posts_discovered_total = Counter("posts_discovered_total", "Discovered posts")
        self.posts_processed_total = Counter("posts_processed_total", "Processed posts")

        self.fetch_http_success_total = Counter("fetch_http_success_total", "HTTP fetch success")
        self.fetch_http_fail_total = Counter("fetch_http_fail_total", "HTTP fetch failures")
        self.fetch_browser_success_total = Counter("fetch_browser_success_total", "Browser fetch success")
        self.fetch_browser_fail_total = Counter("fetch_browser_fail_total", "Browser fetch failures")

        self.ai_calls_total = Counter("ai_calls_total", "AI calls")
        self.ai_fail_total = Counter("ai_fail_total", "AI failures")
        self.ai_latency_seconds = Histogram("ai_latency_seconds", "AI latency", buckets=(0.5, 1, 2, 5, 10, 20, 60, 120, 240))

        self.notifications_sent_total = Counter("notifications_sent_total", "Sent notifications")
        self.notifications_ignored_total = Counter("notifications_ignored_total", "Ignored notifications")

        self.consecutive_failures = Gauge("consecutive_failures", "Consecutive failures", ["type"])

    def start_server(self, bind: str, port: int) -> None:
        start_http_server(port, addr=bind)
        logger.info("metrics server started at %s:%s", bind, port)

    def set_consecutive(self, typ: str, value: int) -> None:
        self.consecutive_failures.labels(type=typ).set(value)


def write_status_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
