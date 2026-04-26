"""
Blog module registration.
"""
from telegram.ext import CommandHandler
from apscheduler.triggers.cron import CronTrigger


NAME = "blog"
DESCRIPTION = "📝 Blog — AI-powered post generation for basheer.net"


def register(bot_app, scheduler, authorized) -> dict:
    from .commands import generate_command, ideas_command, posts_command
    from .jobs import weekly_ideas_job

    bot_app.add_handler(CommandHandler("generate", generate_command, filters=authorized))
    bot_app.add_handler(CommandHandler("ideas",    ideas_command,    filters=authorized))
    bot_app.add_handler(CommandHandler("posts",    posts_command,    filters=authorized))

    scheduler.add_job(
        weekly_ideas_job,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="blog_weekly_ideas",
        replace_existing=True,
    )

    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["generate", "ideas", "posts"],
        "help": [
            "`/generate <topic> [--cat Category]` — generate & publish a blog post",
            "`/ideas` — get 5 content ideas now",
            "`/posts` — show last 5 published posts",
        ],
    }
