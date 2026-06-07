# Module Development Guide

The automation platform uses an **auto-discovery registry**. On startup, the core engine scans the `app/modules/` directory and supports two layouts simultaneously:

| Layout | When to use |
|---|---|
| **Flat** — `modules/<name>/` | Standalone utility with no natural domain peers |
| **Domain-grouped** — `modules/<domain>/<name>/` | When building multiple related submodules (e.g. `ops/analytics`, `ops/maintenance`) |

The rule the registry uses is simple: if the top-level subfolder contains a `register()` function it is a **flat module**; if it does not, it is a **domain namespace** and its children are scanned as submodules.

## 📁 Module Structure

### Flat module (standalone)

```
app/modules/your_module/
├── __init__.py      # Required: The register() entry point
├── models.py        # SQLAlchemy database schemas
├── schemas.py       # Pydantic templates for LLM agent outputs
├── agents.py        # PydanticAI system prompts and logic
├── commands.py      # python-telegram-bot command handlers
└── jobs.py          # APScheduler cron/interval functions
```

### Domain-grouped module

```
app/modules/ops/               # Domain namespace — NO register() here
├── __init__.py                # Empty (or just a docstring)
├── analytics/                 # Submodule
│   ├── __init__.py            # Required: The register() entry point
│   ├── models.py
│   ├── commands.py
│   └── jobs.py
└── maintenance/               # Another submodule in the same domain
    ├── __init__.py
    └── commands.py
```

Each submodule's `register()` is identical in both layouts — the only difference is location on disk. The registry automatically attaches the domain name (e.g. `"ops"`) to the loaded module info for grouping in `/help` and `/start`.

## 🔌 The `register()` Contract

Your leaf module's `__init__.py` **must** export a function named `register`. This function is passed the underlying bot application, the global scheduler, and the authorization filter ensuring only you can invoke the bot.

The contract is identical whether the module is flat or domain-grouped:

```python
# app/modules/<domain>/example/__init__.py  (or app/modules/example/__init__.py)
from telegram.ext import CommandHandler
from apscheduler.triggers.interval import IntervalTrigger

NAME = "example"
DESCRIPTION = "✨ Example — A template module"

def register(bot_app, scheduler, authorized) -> dict:
    from .commands import do_something_command
    from .jobs import background_sync_job

    # 1. Register Telegram Commands
    # Pass 'filters=authorized' to ensure the command is private
    bot_app.add_handler(CommandHandler("dosomething", do_something_command, filters=authorized))

    # 2. Register Scheduled Jobs (Optional)
    scheduler.add_job(
        background_sync_job,
        IntervalTrigger(minutes=60),
        id="example_sync_job",
        replace_existing=True,
    )

    # 3. Return Metadata (Used to build the /help and /start menus)
    #    The registry automatically adds a 'domain' key — do not set it here.
    return {
        "name": NAME,
        "description": DESCRIPTION,
        "commands": ["dosomething"],
        "help": [
            "`/dosomething` — triggers the example action",
        ],
    }
```

## 🛠️ Best Practices

### Database Usage (`models.py`)
All modules share the exact same `SQLite` connection. Always import `Base` from `core.db` to ensure your tables are created at startup.
```python
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from core.db import Base # <-- Crucial

class ExampleData(Base):
    __tablename__ = "example_data"
    id: Mapped[int] = mapped_column(primary_key=True)
```

### Async Telegram Commands (`commands.py`)
Since the core event loop is async, all your telegram commands must be `async def`.
If you need to make API calls or run an LLM, do it directly with await. If an operation takes longer than 2 seconds, consider adding a message update ticker or using the internal queue.

```python
async def my_command(update, context):
    msg = await update.message.reply_text("Thinking...")
    
    # Do work (PydanticAI agent, API call, etc)
    result = await do_long_task()
    
    await context.bot.edit_message_text(
        chat_id=update.effective_chat.id, 
        message_id=msg.message_id,
        text=f"Done! {result}"
    )
```

### PydanticAI Agents (`agents.py`)
To maximize server memory efficiency, instantiate your `Agent` models as module-level singletons, not inside functions. Import the shared LLM endpoint from the core.

```python
from pydantic_ai import Agent
from core.llm import get_model
from .schemas import MyOutputSchema

my_agent = Agent(
    model=get_model(),
    result_type=MyOutputSchema,
    system_prompt="You are a data parsing assistant."
)
```
