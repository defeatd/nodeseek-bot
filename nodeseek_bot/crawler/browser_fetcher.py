from __future__ import annotations

import logging
import time

from nodeseek_bot.crawler.errors import FetchError, ERROR_ANTIBOT, ERROR_LOGIN_REQUIRED, ERROR_TIMEOUT, ERROR_UNKNOWN
from nodeseek_bot.crawler.parser import detect_antibot, detect_login_required, extract_main_text
from nodeseek_bot.ratelimit import MinIntervalLimiter
from nodeseek_bot.storage.db import CONF_FULLTEXT_BROWSER
from nodeseek_bot.storage.types import ContentResult
from nodeseek_bot.utils import collapse_ws, now_utc, sha256_hex


logger = logging.getLogger(__name__)


class PlaywrightPostFetcher:
    def __init__(
        self,
        limiter: MinIntervalLimiter,
        cookie_header: str,
        headless: bool,
        nav_timeout_seconds: int,
    ) -> None:
        self._limiter = limiter
        self._cookie_header = cookie_header
        self._headless = headless
        self._nav_timeout_ms = int(nav_timeout_seconds * 1000)
        self._playwright = None
        self._browser = None

    async def aclose(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except Exception as e:
            raise RuntimeError(
                "Playwright not installed. Install with requirements-playwright.txt and run playwright install."
            ) from e

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)

    async def fetch(self, url: str) -> tuple[ContentResult, dict]:
        await self._limiter.acquire()
        await self._ensure_browser()

        started = time.perf_counter()
        method_meta: dict = {"method": "BROWSER", "http_status": None}

        page = await self._browser.new_page()
        await page.set_extra_http_headers({"Cookie": self._cookie_header})

        try:
            await page.goto(url, timeout=self._nav_timeout_ms, wait_until="domcontentloaded")
            html = await page.content()
        except Exception as e:
            await page.close()
            detail = str(e)
            if "Timeout" in detail or "timeout" in detail:
                raise FetchError(ERROR_TIMEOUT, detail[:240]) from e
            raise FetchError(ERROR_UNKNOWN, detail[:240]) from e

        await page.close()

        if detect_antibot(html):
            raise FetchError(ERROR_ANTIBOT, "antibot/challenge detected")
        if detect_login_required(html):
            raise FetchError(ERROR_LOGIN_REQUIRED, "login required")

        text = collapse_ws(extract_main_text(html))
        result = ContentResult(
            content_text=text,
            content_hash=sha256_hex(text) if text else None,
            content_len=len(text),
            fetched_at=now_utc(),
            source_confidence=CONF_FULLTEXT_BROWSER,
        )

        method_meta["duration_ms"] = int((time.perf_counter() - started) * 1000)
        return result, method_meta
