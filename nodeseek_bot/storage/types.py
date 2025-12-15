from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FeedItem:
    guid: str | None
    url: str
    title: str
    published_at: datetime | None
    summary: str


@dataclass(frozen=True)
class PostRow:
    id: int
    guid: str | None
    url: str
    url_hash: str
    title: str
    published_at: str | None
    rss_summary: str | None
    status: str
    source_confidence: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class FetchAttempt:
    method: str
    ok: bool
    http_status: int | None
    error_type: str | None
    error_detail: str | None
    duration_ms: int | None


@dataclass(frozen=True)
class ContentResult:
    content_text: str | None
    content_html: str | None
    content_hash: str | None
    content_len: int
    fetched_at: datetime | None
    source_confidence: str
    image_urls: list[str]


@dataclass(frozen=True)
class SummaryResult:
    model: str
    prompt_version: str
    summary_text: str
    key_points: list[str]
    actions: list[str]
    image_summaries: list[str]
    token_in: int | None
    token_out: int | None


@dataclass(frozen=True)
class ScoreResult:
    score_total: float
    decision: str
    explain: dict
