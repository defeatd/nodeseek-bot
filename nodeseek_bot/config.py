from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _env_str(name: str, default: str | None = None) -> str:
    value = os.getenv(name)
    if value is None:
        if default is None:
            raise RuntimeError(f"Missing required env: {name}")
        return default
    return value


def _env_int(name: str, default: int | None = None) -> int:
    value = os.getenv(name)
    if value is None:
        if default is None:
            raise RuntimeError(f"Missing required env: {name}")
        return default
    return int(value)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    return value in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Config:
    # Telegram
    bot_token: str
    target_chat_id: int
    admin_user_id: int
    alert_chat_id: int
    tg_parse_mode: str

    # Image summary
    image_summary_enabled: bool
    image_max_count: int
    image_max_bytes: int
    image_total_max_bytes: int
    image_download_timeout_seconds: int
    image_concurrency: int
    image_cookie_host_suffixes: str

    # RSS
    rss_url: str
    rss_interval_seconds: int
    rss_jitter_seconds: int

    # Fulltext fetching
    fulltext_enabled: bool
    nodeseek_cookie: str
    nodeseek_html_min_interval_seconds: int
    nodeseek_html_jitter_seconds: int
    nodeseek_http_timeout_seconds: int
    nodeseek_max_retries: int
    stop_fulltext_on_antibot: bool
    login_backoff_seconds: int
    fulltext_near_threshold_delta: int
    fulltext_fetch_policy: str
    user_agent: str

    # Browser fallback
    allow_browser_fallback: bool
    playwright_headless: bool
    playwright_nav_timeout_seconds: int

    # Rich text extraction (reconstruct Markdown-like structure from HTML)
    rich_text_enabled: bool
    rich_text_max_chars: int
    rich_text_max_code_blocks: int
    rich_text_max_code_chars_total: int
    rich_text_max_table_rows: int
    rich_text_max_links: int

    # AI
    ai_base_url: str
    ai_api_key: str
    ai_model: str
    ai_timeout_seconds: int
    ai_max_retries: int
    ai_prefer_chat_completions: bool
    ai_fallback_to_responses: bool
    ai_max_input_chars: int
    ai_chunk_chars: int
    ai_chunk_overlap_chars: int

    # Rules
    rules_path: Path
    rules_overrides_path: Path

    # Storage
    sqlite_path: Path
    data_retention_days: int
    fingerprint_retention_days: int

    # Metrics
    metrics_enabled: bool
    metrics_bind: str
    metrics_port: int
    status_json_path: Path

    # Alerts
    alert_n_fetch: int
    alert_n_login: int
    alert_n_ai: int

    # Logging
    log_level: str
    log_file: str


def load_config() -> Config:
    return Config(
        bot_token=_env_str("BOT_TOKEN"),
        target_chat_id=_env_int("TARGET_CHAT_ID", -1003697568105),
        admin_user_id=_env_int("ADMIN_USER_ID", 1443986987),
        alert_chat_id=_env_int("ALERT_CHAT_ID", 1443986987),
        tg_parse_mode=_env_str("TG_PARSE_MODE", "HTML"),
        image_summary_enabled=_env_bool("IMAGE_SUMMARY_ENABLED", True),
        image_max_count=_env_int("IMAGE_MAX_COUNT", 10),
        image_max_bytes=_env_int("IMAGE_MAX_BYTES", 1500000),
        image_total_max_bytes=_env_int("IMAGE_TOTAL_MAX_BYTES", 8000000),
        image_download_timeout_seconds=_env_int("IMAGE_DOWNLOAD_TIMEOUT_SECONDS", 20),
        image_concurrency=_env_int("IMAGE_CONCURRENCY", 3),
        image_cookie_host_suffixes=_env_str("IMAGE_COOKIE_HOST_SUFFIXES", "nodeseek.com"),
        rss_url=_env_str("RSS_URL", "https://rss.nodeseek.com/"),
        rss_interval_seconds=_env_int("RSS_INTERVAL_SECONDS", 60),
        rss_jitter_seconds=_env_int("RSS_JITTER_SECONDS", 10),
        fulltext_enabled=_env_bool("FULLTEXT_ENABLED", True),
        nodeseek_cookie=_env_str("NODESEEK_COOKIE", ""),
        nodeseek_html_min_interval_seconds=_env_int("NODESEEK_HTML_MIN_INTERVAL_SECONDS", 60),
        nodeseek_html_jitter_seconds=_env_int("NODESEEK_HTML_JITTER_SECONDS", 15),
        nodeseek_http_timeout_seconds=_env_int("NODESEEK_HTTP_TIMEOUT_SECONDS", 30),
        nodeseek_max_retries=_env_int("NODESEEK_MAX_RETRIES", 2),
        stop_fulltext_on_antibot=_env_bool("STOP_FULLTEXT_ON_ANTIBOT", True),
        login_backoff_seconds=_env_int("LOGIN_BACKOFF_SECONDS", 3600),
        fulltext_near_threshold_delta=_env_int("FULLTEXT_NEAR_THRESHOLD_DELTA", 4),
        fulltext_fetch_policy=_env_str("FULLTEXT_FETCH_POLICY", "near_threshold"),
        user_agent=_env_str(
            "USER_AGENT",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        ),
        allow_browser_fallback=_env_bool("ALLOW_BROWSER_FALLBACK", True),
        playwright_headless=_env_bool("PLAYWRIGHT_HEADLESS", True),
        playwright_nav_timeout_seconds=_env_int("PLAYWRIGHT_NAV_TIMEOUT_SECONDS", 45),
        rich_text_enabled=_env_bool("RICH_TEXT_ENABLED", True),
        rich_text_max_chars=_env_int("RICH_TEXT_MAX_CHARS", 20000),
        rich_text_max_code_blocks=_env_int("RICH_TEXT_MAX_CODE_BLOCKS", 6),
        rich_text_max_code_chars_total=_env_int("RICH_TEXT_MAX_CODE_CHARS_TOTAL", 6000),
        rich_text_max_table_rows=_env_int("RICH_TEXT_MAX_TABLE_ROWS", 30),
        rich_text_max_links=_env_int("RICH_TEXT_MAX_LINKS", 40),
        ai_base_url=_env_str("AI_BASE_URL", ""),
        ai_api_key=_env_str("AI_API_KEY", ""),
        ai_model=_env_str("AI_MODEL", ""),
        ai_timeout_seconds=_env_int("AI_TIMEOUT_SECONDS", 180),
        ai_max_retries=_env_int("AI_MAX_RETRIES", 2),
        ai_prefer_chat_completions=_env_bool("AI_PREFER_CHAT_COMPLETIONS", True),
        ai_fallback_to_responses=_env_bool("AI_FALLBACK_TO_RESPONSES", True),
        ai_max_input_chars=_env_int("AI_MAX_INPUT_CHARS", 200000),
        ai_chunk_chars=_env_int("AI_CHUNK_CHARS", 60000),
        ai_chunk_overlap_chars=_env_int("AI_CHUNK_OVERLAP_CHARS", 1500),
        rules_path=Path(_env_str("RULES_PATH", "rules/rules.yaml")),
        rules_overrides_path=Path(_env_str("RULES_OVERRIDES_PATH", "rules/overrides.yaml")),
        sqlite_path=Path(_env_str("SQLITE_PATH", "data/nodeseek.db")),
        data_retention_days=_env_int("DATA_RETENTION_DAYS", 7),
        fingerprint_retention_days=_env_int("FINGERPRINT_RETENTION_DAYS", 3650),
        metrics_enabled=_env_bool("METRICS_ENABLED", True),
        metrics_bind=_env_str("METRICS_BIND", "127.0.0.1"),
        metrics_port=_env_int("METRICS_PORT", 9108),
        status_json_path=Path(_env_str("STATUS_JSON_PATH", "data/status.json")),
        alert_n_fetch=_env_int("ALERT_N_FETCH", 5),
        alert_n_login=_env_int("ALERT_N_LOGIN", 3),
        alert_n_ai=_env_int("ALERT_N_AI", 5),
        log_level=_env_str("LOG_LEVEL", "INFO"),
        log_file=_env_str("LOG_FILE", ""),
    )
