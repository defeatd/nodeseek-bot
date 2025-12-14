from __future__ import annotations

import logging
import time

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter, retry_if_exception_type

from nodeseek_bot.crawler.errors import (
    FetchError,
    ERROR_ANTIBOT,
    ERROR_HTTP,
    ERROR_LOGIN_REQUIRED,
    ERROR_TIMEOUT,
    ERROR_UNKNOWN,
)
from nodeseek_bot.crawler.parser import detect_antibot, detect_login_required, extract_main_text
from nodeseek_bot.ratelimit import MinIntervalLimiter
from nodeseek_bot.storage.db import CONF_FULLTEXT_HTTP, CONF_RSS_ONLY
from nodeseek_bot.storage.types import ContentResult
from nodeseek_bot.utils import collapse_ws, now_utc, sha256_hex


logger = logging.getLogger(__name__)


def _redact_detail(detail: str) -> str:
    detail = detail.strip()
    if len(detail) > 240:
        detail = detail[:240] + "…"
    return detail


class HttpPostFetcher:
    def __init__(
        self,
        limiter: MinIntervalLimiter,
        cookie_header: str,
        timeout_seconds: int,
        max_retries: int,
        user_agent: str,
    ) -> None:
        self._limiter = limiter
        self._cookie_header = cookie_header
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._user_agent = user_agent

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Cookie": cookie_header,
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.TransportError, httpx.ReadTimeout)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1, max=10),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        return await self._client.get(url)

    async def fetch(self, url: str) -> tuple[ContentResult, dict]:
        await self._limiter.acquire()
        started = time.perf_counter()
        method_meta: dict = {"method": "HTTP", "http_status": None}

        try:
            resp = await self._get(url)
            method_meta["http_status"] = resp.status_code
        except httpx.ReadTimeout as e:
            raise FetchError(ERROR_TIMEOUT, _redact_detail(str(e))) from e
        except httpx.TransportError as e:
            raise FetchError(ERROR_HTTP, _redact_detail(str(e))) from e
        except Exception as e:  # pragma: no cover
            raise FetchError(ERROR_UNKNOWN, _redact_detail(str(e))) from e

        if resp.status_code == 429:
            raise FetchError(ERROR_HTTP, "429 too many requests")
        if resp.status_code >= 500:
            raise FetchError(ERROR_HTTP, f"{resp.status_code} server error")
        if resp.status_code >= 400:
            raise FetchError(ERROR_HTTP, f"{resp.status_code} client error")

        html = resp.text

        if detect_antibot(html):
            raise FetchError(ERROR_ANTIBOT, "antibot/challenge detected")

        if detect_login_required(html):
            raise FetchError(ERROR_LOGIN_REQUIRED, "login required")

        text = extract_main_text(html)
        text = collapse_ws(text)
        content_hash = sha256_hex(text) if text else None
        content_len = len(text)
        fetched_at = now_utc()

        duration_ms = int((time.perf_counter() - started) * 1000)
        method_meta["duration_ms"] = duration_ms

        result = ContentResult(
            content_text=text,
            content_hash=content_hash,
            content_len=content_len,
            fetched_at=fetched_at,
            source_confidence=CONF_FULLTEXT_HTTP,
        )
        return result, method_meta
