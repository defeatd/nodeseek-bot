from __future__ import annotations

import logging
import time

from nodeseek_bot.crawler.errors import FetchError, ERROR_ANTIBOT, ERROR_LOGIN_REQUIRED, ERROR_UNKNOWN
from nodeseek_bot.storage.db import CONF_RSS_ONLY
from nodeseek_bot.storage.types import ContentResult, FetchAttempt
from nodeseek_bot.utils import now_utc, sha256_hex


logger = logging.getLogger(__name__)


class FulltextDisabledState:
    def __init__(self) -> None:
        self._disabled_until_ts: float | None = None
        self._disabled_forever: bool = False

    def disable_for_seconds(self, seconds: int) -> None:
        self._disabled_forever = False
        self._disabled_until_ts = time.time() + max(0, seconds)

    def disable_forever(self) -> None:
        self._disabled_forever = True
        self._disabled_until_ts = None

    def enable(self) -> None:
        self._disabled_forever = False
        self._disabled_until_ts = None

    def is_disabled(self) -> bool:
        if self._disabled_forever:
            return True
        return self._disabled_until_ts is not None and time.time() < self._disabled_until_ts


class CrawlerService:
    def __init__(
        self,
        http_fetcher,
        browser_fetcher,
        stop_fulltext_on_antibot: bool,
        login_backoff_seconds: int,
        allow_browser_fallback: bool,
    ) -> None:
        self._http = http_fetcher
        self._browser = browser_fetcher
        self._stop_on_antibot = stop_fulltext_on_antibot
        self._login_backoff_seconds = login_backoff_seconds
        self._allow_browser = allow_browser_fallback
        self._disabled = FulltextDisabledState()

    def fulltext_disabled(self) -> bool:
        return self._disabled.is_disabled()

    def enable_fulltext(self) -> None:
        self._disabled.enable()

    def disable_fulltext_for_seconds(self, seconds: int) -> None:
        self._disabled.disable_for_seconds(seconds)

    def disable_fulltext_forever(self) -> None:
        self._disabled.disable_forever()

    def _rss_only(self, rss_fallback_text: str) -> ContentResult:
        text = rss_fallback_text
        return ContentResult(
            content_text=text,
            content_hash=sha256_hex(text) if text else None,
            content_len=len(text),
            fetched_at=now_utc(),
            source_confidence=CONF_RSS_ONLY,
        )

    async def fetch_best_effort(self, url: str, rss_fallback_text: str) -> tuple[ContentResult, list[FetchAttempt]]:
        """Fetch fulltext best-effort.

        This method should *not* raise for common fetch/login/antibot failures.
        It returns RSS_ONLY content with attempts recorded, and may disable fulltext
        automatically when antibot/login-required is detected.
        """

        attempts: list[FetchAttempt] = []

        if self._disabled.is_disabled():
            return self._rss_only(rss_fallback_text), attempts

        # 1) HTTP fetch
        try:
            result, meta = await self._http.fetch(url)
            attempts.append(
                FetchAttempt(
                    method="HTTP",
                    ok=True,
                    http_status=meta.get("http_status"),
                    error_type=None,
                    error_detail=None,
                    duration_ms=meta.get("duration_ms"),
                )
            )
            return result, attempts
        except FetchError as e:
            attempts.append(
                FetchAttempt(
                    method="HTTP",
                    ok=False,
                    http_status=None,
                    error_type=e.error_type,
                    error_detail=e.detail,
                    duration_ms=None,
                )
            )

            if e.error_type == ERROR_ANTIBOT and self._stop_on_antibot:
                # Safer: stop fulltext until manual re-enable.
                self._disabled.disable_forever()
                return self._rss_only(rss_fallback_text), attempts

            if e.error_type == ERROR_LOGIN_REQUIRED and self._stop_on_antibot:
                # Cookie expired / permission issue: backoff for a while.
                self._disabled.disable_for_seconds(self._login_backoff_seconds)
                return self._rss_only(rss_fallback_text), attempts

        # 2) Browser fallback (only for non-login/antibot cases)
        if self._allow_browser and self._browser is not None:
            try:
                result, meta = await self._browser.fetch(url)
                attempts.append(
                    FetchAttempt(
                        method="BROWSER",
                        ok=True,
                        http_status=meta.get("http_status"),
                        error_type=None,
                        error_detail=None,
                        duration_ms=meta.get("duration_ms"),
                    )
                )
                return result, attempts
            except FetchError as e:
                attempts.append(
                    FetchAttempt(
                        method="BROWSER",
                        ok=False,
                        http_status=None,
                        error_type=e.error_type,
                        error_detail=e.detail,
                        duration_ms=None,
                    )
                )

                if e.error_type == ERROR_ANTIBOT and self._stop_on_antibot:
                    self._disabled.disable_forever()
                    return self._rss_only(rss_fallback_text), attempts

                if e.error_type == ERROR_LOGIN_REQUIRED and self._stop_on_antibot:
                    self._disabled.disable_for_seconds(self._login_backoff_seconds)
                    return self._rss_only(rss_fallback_text), attempts
            except Exception as e:
                attempts.append(
                    FetchAttempt(
                        method="BROWSER",
                        ok=False,
                        http_status=None,
                        error_type=ERROR_UNKNOWN,
                        error_detail=str(e)[:240],
                        duration_ms=None,
                    )
                )

        return self._rss_only(rss_fallback_text), attempts

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
        if self._browser is not None:
            await self._browser.aclose()
