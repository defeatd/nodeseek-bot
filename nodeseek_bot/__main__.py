from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ApplicationBuilder

from nodeseek_bot.config import load_config
from nodeseek_bot.logging_setup import setup_logging
from nodeseek_bot.jobs.pipeline import build_app_context, start_background_jobs, stop_background_jobs
from nodeseek_bot.telegram.bot import register_handlers


logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="nodeseek-bot")
    parser.add_argument(
        "--env",
        default=".env",
        help="Path to .env file (default: .env).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    env_path = Path(args.env)
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    config = load_config()
    setup_logging(config.log_level, config.log_file)

    async def post_init(application: Application) -> None:
        ctx = await build_app_context(config, application)
        application.bot_data["ctx"] = ctx
        await start_background_jobs(application, ctx)
        logger.info("bot started")

    async def post_shutdown(application: Application) -> None:
        ctx = application.bot_data.get("ctx")
        if ctx is not None:
            await stop_background_jobs(application, ctx)
        logger.info("bot stopped")

    application = (
        ApplicationBuilder()
        .token(config.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    register_handlers(application)

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
