from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from nodeseek_bot.rules.loader import load_yaml, save_overrides


logger = logging.getLogger(__name__)


def _get_ctx(application: Application):
    ctx = application.bot_data.get("ctx")
    if ctx is None:
        raise RuntimeError("app context not initialized")
    return ctx


def _is_admin(update: Update, admin_user_id: int) -> bool:
    u = update.effective_user
    return u is not None and u.id == admin_user_id


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return

    stats = ctx.runtime_stats
    next_html = ctx.html_limiter.next_allowed_in_seconds()

    text = (
        f"paused={stats.paused}\n"
        f"fulltext_disabled={stats.fulltext_disabled}\n"
        f"html_next_allowed_in={next_html:.0f}s\n"
        f"consecutive_fetch_failures={stats.consecutive_fetch_failures}\n"
        f"consecutive_login_failures={stats.consecutive_login_failures}\n"
        f"consecutive_ai_failures={stats.consecutive_ai_failures}\n"
    )
    await update.message.reply_text(text)


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return
    ctx.paused = True
    ctx.runtime_stats.paused = True
    await update.message.reply_text("已暂停")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return
    ctx.paused = False
    ctx.runtime_stats.paused = False
    await update.message.reply_text("已恢复")


async def cmd_rules_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return
    await ctx.reload_rules()
    await update.message.reply_text("规则已重载")


async def cmd_set_threshold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return

    if not context.args:
        await update.message.reply_text("用法：/set_threshold <n>")
        return

    try:
        val = float(context.args[0])
    except ValueError:
        await update.message.reply_text("阈值必须是数字")
        return

    data = load_yaml(ctx.config.rules_overrides_path)
    data.setdefault("version", 1)
    data["score_threshold"] = val
    save_overrides(ctx.config.rules_overrides_path, data)
    await ctx.reload_rules()
    await update.message.reply_text(f"阈值已更新为 {val}")


async def cmd_whitelist_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return
    if not context.args:
        await update.message.reply_text("用法：/whitelist_add <kw>")
        return
    kw = " ".join(context.args).strip()
    if not kw:
        return

    data = load_yaml(ctx.config.rules_overrides_path)
    data.setdefault("version", 1)
    data.setdefault("keywords", {}).setdefault("whitelist", [])
    if kw not in data["keywords"]["whitelist"]:
        data["keywords"]["whitelist"].append(kw)
    save_overrides(ctx.config.rules_overrides_path, data)
    await ctx.reload_rules()
    await update.message.reply_text(f"已加入白名单：{kw}")


async def cmd_blacklist_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return
    if not context.args:
        await update.message.reply_text("用法：/blacklist_add <kw>")
        return
    kw = " ".join(context.args).strip()
    if not kw:
        return

    data = load_yaml(ctx.config.rules_overrides_path)
    data.setdefault("version", 1)
    data.setdefault("keywords", {}).setdefault("blacklist", [])
    if kw not in data["keywords"]["blacklist"]:
        data["keywords"]["blacklist"].append(kw)
    save_overrides(ctx.config.rules_overrides_path, data)
    await ctx.reload_rules()
    await update.message.reply_text(f"已加入黑名单：{kw}")


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return

    limit = 10
    if context.args:
        try:
            limit = max(1, min(30, int(context.args[0])))
        except ValueError:
            pass

    rows = await ctx.storage.list_recent_posts(limit=limit)
    if not rows:
        await update.message.reply_text("暂无记录")
        return

    lines = []
    for r in rows:
        s = await ctx.storage.load_score(r.id)
        score_text = f"{s.score_total:.1f} {s.decision}" if s else "(no score)"
        lines.append(f"#{r.id} {score_text} {r.title} {r.url}")

    await update.message.reply_text("\n".join(lines[:limit]))


async def cmd_reprocess(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        return

    if not context.args:
        await update.message.reply_text("用法：/reprocess <post_id>")
        return
    try:
        post_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("post_id 必须是整数")
        return

    ok = await ctx.reset_post(post_id)
    await update.message.reply_text("已重置并加入队列" if ok else "post_id 不存在")


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    ctx = _get_ctx(context.application)
    if not _is_admin(update, ctx.config.admin_user_id):
        await query.answer("无权限", show_alert=True)
        return

    data = query.data or ""

    m = re.match(r"^(noise|block_title):(?P<post_id>\d+)$", data)
    if not m:
        await query.answer("未知操作", show_alert=True)
        return

    post_id = int(m.group("post_id"))
    action = m.group(1)

    if action == "noise":
        await ctx.mark_noise(post_id)
        await query.answer("已标记为噪音")
        return

    if action == "block_title":
        post = await ctx.storage.get_post(post_id)
        if post is None:
            await query.answer("帖子不存在", show_alert=True)
            return

        pat = r"^" + re.escape(post.title) + r"$"
        ovr = load_yaml(ctx.config.rules_overrides_path)
        ovr.setdefault("version", 1)
        ovr.setdefault("block_title_regex", [])
        if pat not in ovr["block_title_regex"]:
            ovr["block_title_regex"].append(pat)
        save_overrides(ctx.config.rules_overrides_path, ovr)
        await ctx.reload_rules()
        await query.answer("已加入标题黑名单")
        return


def build_inline_keyboard(post_id: int, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("打开原帖", url=url)],
            [
                InlineKeyboardButton("标记噪音", callback_data=f"noise:{post_id}"),
                InlineKeyboardButton("加入黑名单(标题)", callback_data=f"block_title:{post_id}"),
            ],
        ]
    )


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("set_threshold", cmd_set_threshold))
    app.add_handler(CommandHandler("whitelist_add", cmd_whitelist_add))
    app.add_handler(CommandHandler("blacklist_add", cmd_blacklist_add))
    app.add_handler(CommandHandler("rules_reload", cmd_rules_reload))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("reprocess", cmd_reprocess))

    app.add_handler(CallbackQueryHandler(on_callback))
