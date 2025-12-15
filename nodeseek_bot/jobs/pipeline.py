from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass

from telegram.ext import Application

from nodeseek_bot.ai.client import AIConfig, OpenAICompatClient
from nodeseek_bot.config import Config
from nodeseek_bot.crawler.errors import ERROR_LOGIN_REQUIRED
from nodeseek_bot.crawler.browser_fetcher import PlaywrightPostFetcher
from nodeseek_bot.crawler.http_fetcher import HttpPostFetcher
from nodeseek_bot.crawler.service import CrawlerService
from nodeseek_bot.metrics.metrics import Metrics, RuntimeStats, write_status_json
from nodeseek_bot.ratelimit import MinIntervalLimiter
from nodeseek_bot.rss.async_poller import AsyncRssPoller
from nodeseek_bot.rules.engine import RuleEngine
from nodeseek_bot.rules.loader import load_rules
from nodeseek_bot.storage.db import Storage, STATUS_FAILED, STATUS_IGNORED
from nodeseek_bot.telegram.alerts import maybe_send_consecutive_failure_alert
from nodeseek_bot.telegram.bot import build_inline_keyboard
from nodeseek_bot.telegram.render import render_message
from nodeseek_bot.utils import collapse_ws


_MIN_LABELS_TO_AUTOFILTER = 10000


logger = logging.getLogger(__name__)


@dataclass
class AppContext:
    config: Config
    storage: Storage
    rss: AsyncRssPoller
    crawler: CrawlerService
    ai: OpenAICompatClient
    rules: RuleEngine
    metrics: Metrics
    runtime_stats: RuntimeStats
    html_limiter: MinIntervalLimiter

    paused: bool = False

    async def reload_rules(self) -> None:
        rules = load_rules(self.config.rules_path, self.config.rules_overrides_path)
        self.rules = RuleEngine(rules)

    async def reset_post(self, post_id: int) -> bool:
        post = await self.storage.get_post(post_id)
        if post is None:
            return False
        await self.storage.reset_post(post_id)
        return True

    async def save_label(self, post_id: int, label: str) -> None:
        await self.storage.upsert_label(post_id, label, labeled_by=self.config.admin_user_id)


async def build_app_context(config: Config, application: Application) -> AppContext:
    storage = Storage(config.sqlite_path)
    await storage.connect()

    metrics = Metrics()
    if config.metrics_enabled:
        metrics.start_server(config.metrics_bind, config.metrics_port)

    html_limiter = MinIntervalLimiter(
        min_interval_seconds=config.nodeseek_html_min_interval_seconds,
        jitter_seconds=config.nodeseek_html_jitter_seconds,
    )

    cookie = config.nodeseek_cookie
    http_fetcher = HttpPostFetcher(
        limiter=html_limiter,
        cookie_header=cookie,
        timeout_seconds=config.nodeseek_http_timeout_seconds,
        max_retries=config.nodeseek_max_retries,
        user_agent=config.user_agent,
    )

    browser_fetcher = None
    if config.allow_browser_fallback:
        browser_fetcher = PlaywrightPostFetcher(
            limiter=html_limiter,
            cookie_header=cookie,
            headless=config.playwright_headless,
            nav_timeout_seconds=config.playwright_nav_timeout_seconds,
        )

    crawler = CrawlerService(
        http_fetcher=http_fetcher,
        browser_fetcher=browser_fetcher,
        stop_fulltext_on_antibot=config.stop_fulltext_on_antibot,
        login_backoff_seconds=config.login_backoff_seconds,
        allow_browser_fallback=config.allow_browser_fallback,
    )

    ai = OpenAICompatClient(
        AIConfig(
            base_url=config.ai_base_url,
            api_key=config.ai_api_key,
            model=config.ai_model,
            timeout_seconds=config.ai_timeout_seconds,
            max_retries=config.ai_max_retries,
            prefer_chat_completions=config.ai_prefer_chat_completions,
            fallback_to_responses=config.ai_fallback_to_responses,
            max_input_chars=config.ai_max_input_chars,
            chunk_chars=config.ai_chunk_chars,
            chunk_overlap_chars=config.ai_chunk_overlap_chars,
        )
    )

    rules_dict = load_rules(config.rules_path, config.rules_overrides_path)
    rules = RuleEngine(rules_dict)

    return AppContext(
        config=config,
        storage=storage,
        rss=AsyncRssPoller(config.rss_url),
        crawler=crawler,
        ai=ai,
        rules=rules,
        metrics=metrics,
        runtime_stats=RuntimeStats(),
        html_limiter=html_limiter,
        paused=False,
    )


async def start_background_jobs(application: Application, ctx: AppContext) -> None:
    async def rss_job() -> None:
        while True:
            try:
                await poll_rss_once(application, ctx)
            except Exception:
                logger.exception("rss job failed")
            await asyncio.sleep(ctx.config.rss_interval_seconds)

    async def process_job() -> None:
        while True:
            try:
                await process_one(application, ctx)
            except Exception:
                logger.exception("process job failed")
            await asyncio.sleep(10)

    async def cleanup_job() -> None:
        while True:
            try:
                await ctx.storage.cleanup(ctx.config.data_retention_days, ctx.config.fingerprint_retention_days)
            except Exception:
                logger.exception("cleanup job failed")
            await asyncio.sleep(3600)

    async def status_job() -> None:
        while True:
            try:
                await write_status(application, ctx)
            except Exception:
                logger.exception("status job failed")
            await asyncio.sleep(30)

    ctx._tasks = [
        asyncio.create_task(rss_job(), name="rss_job"),
        asyncio.create_task(process_job(), name="process_job"),
        asyncio.create_task(cleanup_job(), name="cleanup_job"),
        asyncio.create_task(status_job(), name="status_job"),
    ]


async def stop_background_jobs(application: Application, ctx: AppContext) -> None:
    tasks = getattr(ctx, "_tasks", [])
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await ctx.ai.aclose()
    await ctx.crawler.aclose()
    await ctx.rss.aclose()
    await ctx.storage.close()


async def poll_rss_once(application: Application, ctx: AppContext) -> None:
    # RSS jitter (RSS 请求不计入站点 HTML 限速)
    if ctx.config.rss_jitter_seconds > 0:
        await asyncio.sleep(random.uniform(0.0, float(ctx.config.rss_jitter_seconds)))

    ctx.metrics.rss_polls_total.inc()
    items = await ctx.rss.poll()
    ctx.runtime_stats.last_rss_poll_ts = time.time()

    discovered = 0
    for it in items:
        await ctx.storage.upsert_from_feed(it)
        discovered += 1

    ctx.metrics.posts_discovered_total.inc(discovered)
    logger.info("rss poll: %s items", discovered)


def _should_attempt_fulltext(ctx: AppContext, title: str, rss_text: str) -> bool:
    if not ctx.config.fulltext_enabled:
        return False
    if not ctx.config.nodeseek_cookie:
        return False
    if ctx.crawler.fulltext_disabled():
        return False

    policy = (ctx.config.fulltext_fetch_policy or "near_threshold").strip().lower()
    if policy == "never":
        return False
    if policy == "always":
        return True

    # near_threshold: quick score on RSS-only text
    s = ctx.rules.score(title=title, text=rss_text, source_confidence="RSS_ONLY")
    if s.decision == "WHITELIST":
        return True
    threshold = float(s.explain.get("threshold", 18))
    delta = float(ctx.config.fulltext_near_threshold_delta)
    return s.score_total >= (threshold - delta)


def _update_fetch_stats_and_metrics(ctx: AppContext, attempts: list) -> None:
    if not attempts:
        return

    any_ok = any(getattr(a, "ok", False) for a in attempts)
    if any_ok:
        ctx.runtime_stats.consecutive_fetch_failures = 0
    else:
        ctx.runtime_stats.consecutive_fetch_failures += 1

    login_failed = any(getattr(a, "error_type", None) == ERROR_LOGIN_REQUIRED for a in attempts)
    if login_failed:
        ctx.runtime_stats.consecutive_login_failures += 1
    elif any_ok:
        ctx.runtime_stats.consecutive_login_failures = 0

    ctx.metrics.set_consecutive("fetch", ctx.runtime_stats.consecutive_fetch_failures)
    ctx.metrics.set_consecutive("login", ctx.runtime_stats.consecutive_login_failures)

    for a in attempts:
        method = getattr(a, "method", "")
        ok = bool(getattr(a, "ok", False))
        if method == "HTTP":
            (ctx.metrics.fetch_http_success_total if ok else ctx.metrics.fetch_http_fail_total).inc()
        if method == "BROWSER":
            (ctx.metrics.fetch_browser_success_total if ok else ctx.metrics.fetch_browser_fail_total).inc()


async def _compute_best_threshold(labeled: list[tuple[float, int]]) -> float:
    if not labeled:
        return float("inf")

    # Sort descending by score
    labeled_sorted = sorted(labeled, key=lambda x: float(x[0]), reverse=True)
    total_pos = sum(1 for _, y in labeled_sorted if int(y) == 1)
    total = len(labeled_sorted)

    # If no useful labels, prefer predicting none.
    if total_pos <= 0:
        return float("inf")

    # If all useful, predict all.
    if total_pos >= total:
        return float(labeled_sorted[-1][0])

    best_f1 = -1.0
    best_threshold = float(labeled_sorted[0][0])

    tp = 0
    fp = 0

    idx = 0
    while idx < total:
        score_val = float(labeled_sorted[idx][0])

        # Include all items with this score
        while idx < total and float(labeled_sorted[idx][0]) == score_val:
            y = int(labeled_sorted[idx][1])
            if y == 1:
                tp += 1
            else:
                fp += 1
            idx += 1

        fn = total_pos - tp
        denom = (2 * tp + fp + fn)
        f1 = (2 * tp / denom) if denom > 0 else 0.0

        if f1 > best_f1:
            best_f1 = f1
            best_threshold = score_val

    return best_threshold


async def _should_deliver(ctx: AppContext, score_total: float, decision: str) -> bool:
    if decision == "BLACKLIST":
        return False

    n_labels = await ctx.storage.count_labels()
    if n_labels < _MIN_LABELS_TO_AUTOFILTER:
        return True

    labeled_scores = await ctx.storage.get_labeled_scores(limit=_MIN_LABELS_TO_AUTOFILTER)
    threshold = await _compute_best_threshold(labeled_scores)

    if threshold == float("inf"):
        return False

    if decision == "WHITELIST":
        return True

    return float(score_total) >= float(threshold)


async def process_one(application: Application, ctx: AppContext) -> None:
    if ctx.paused:
        return

    post_id = await ctx.storage.take_next_for_processing()
    if post_id is None:
        return

    post = await ctx.storage.get_post(post_id)
    if post is None:
        return

    ctx.runtime_stats.last_processed_post_id = post_id

    rss_text = collapse_ws((post.rss_summary or "") + "\n" + post.title)

    content_text = rss_text
    source_conf = "RSS_ONLY"
    attempts = []

    tried_fulltext = False
    if _should_attempt_fulltext(ctx, post.title, rss_text):
        tried_fulltext = True
        try:
            content_result, attempts = await ctx.crawler.fetch_best_effort(post.url, rss_text)
            await ctx.storage.save_content(post_id, content_result)
            source_conf = content_result.source_confidence
            content_text = content_result.content_text or rss_text
        except Exception as e:
            await ctx.storage.set_status(post_id, STATUS_FAILED)
            ctx.runtime_stats.consecutive_fetch_failures += 1
            ctx.metrics.set_consecutive("fetch", ctx.runtime_stats.consecutive_fetch_failures)
            await maybe_send_consecutive_failure_alert(
                application,
                ctx.config.alert_chat_id,
                "抓取/登录",
                ctx.runtime_stats.consecutive_fetch_failures,
                ctx.config.alert_n_fetch,
            )
            logger.warning("fulltext fetch failed post_id=%s err=%s", post_id, e)
            content_text = rss_text
            source_conf = "RSS_ONLY"

    # Update fetch/login stats and metrics
    if tried_fulltext:
        _update_fetch_stats_and_metrics(ctx, attempts)
        await maybe_send_consecutive_failure_alert(
            application,
            ctx.config.alert_chat_id,
            "登录/Cookie",
            ctx.runtime_stats.consecutive_login_failures,
            ctx.config.alert_n_login,
        )

    # Record attempts
    for idx, a in enumerate(attempts, start=1):
        await ctx.storage.add_fetch_attempt(post_id, idx, a)

    # AI summary
    summary = await ctx.storage.load_summary(post_id)
    if summary is None:
        ctx.metrics.ai_calls_total.inc()
        try:
            with ctx.metrics.ai_latency_seconds.time():
                summary = await ctx.ai.summarize(post.title, post.url, content_text)
            await ctx.storage.save_summary(post_id, summary)
            ctx.runtime_stats.consecutive_ai_failures = 0
            ctx.metrics.set_consecutive("ai", 0)
        except Exception as e:
            ctx.metrics.ai_fail_total.inc()
            ctx.runtime_stats.consecutive_ai_failures += 1
            ctx.metrics.set_consecutive("ai", ctx.runtime_stats.consecutive_ai_failures)
            await maybe_send_consecutive_failure_alert(
                application,
                ctx.config.alert_chat_id,
                "AI 总结",
                ctx.runtime_stats.consecutive_ai_failures,
                ctx.config.alert_n_ai,
            )
            logger.warning("ai summarize failed post_id=%s err=%s", post_id, e)
            summary = None

    # Score (include AI-extracted text to improve recall)
    score_input = content_text
    if summary is not None:
        score_input = score_input + "\n\n" + summary.summary_text + "\n" + "\n".join(summary.key_points + summary.actions)

    score = ctx.rules.score(title=post.title, text=score_input, source_confidence=source_conf)
    await ctx.storage.save_score(post_id, score)

    # Decide delivery
    deliver = await _should_deliver(ctx, score.score_total, score.decision)
    if deliver:
        if not await ctx.storage.has_delivery(post_id, ctx.config.target_chat_id):
            msg = render_message(post, summary, score)
            keyboard = build_inline_keyboard(post_id)
            sent = await application.bot.send_message(
                chat_id=ctx.config.target_chat_id,
                text=msg,
                parse_mode=ctx.config.tg_parse_mode,
                disable_web_page_preview=True,
                reply_markup=keyboard,
            )
            await ctx.storage.record_delivery(post_id, ctx.config.target_chat_id, sent.message_id)
            ctx.metrics.notifications_sent_total.inc()
        await ctx.storage.update_fingerprint_processed(post.url_hash, score.decision)
    else:
        await ctx.storage.set_status(post_id, STATUS_IGNORED)
        await ctx.storage.update_fingerprint_processed(post.url_hash, score.decision)
        ctx.metrics.notifications_ignored_total.inc()

    ctx.metrics.posts_processed_total.inc()


async def write_status(application: Application, ctx: AppContext) -> None:
    stats = ctx.runtime_stats
    stats.paused = ctx.paused
    stats.fulltext_disabled = ctx.crawler.fulltext_disabled()
    stats.html_next_allowed_in_seconds = ctx.html_limiter.next_allowed_in_seconds()

    ctx.metrics.set_consecutive("fetch", stats.consecutive_fetch_failures)
    ctx.metrics.set_consecutive("login", stats.consecutive_login_failures)
    ctx.metrics.set_consecutive("ai", stats.consecutive_ai_failures)

    data = {
        "paused": stats.paused,
        "fulltext_disabled": stats.fulltext_disabled,
        "html_next_allowed_in_seconds": stats.html_next_allowed_in_seconds,
        "last_rss_poll_ts": stats.last_rss_poll_ts,
        "last_processed_post_id": stats.last_processed_post_id,
        "consecutive_failures": {
            "fetch": stats.consecutive_fetch_failures,
            "login": stats.consecutive_login_failures,
            "ai": stats.consecutive_ai_failures,
        },
    }
    write_status_json(ctx.config.status_json_path, data)
