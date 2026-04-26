"""
Module registry — auto-discovers and loads all packages under modules/.

Supports two layouts, and both can coexist without any module changes:

  Flat   (legacy) : modules/<name>/
      The module's __init__.py has register() directly.
      No domain is attached; shows as a standalone entry in /help.

  Domain-grouped  : modules/<domain>/<name>/
      The domain folder has NO register() — it is just a namespace.
      Each submodule's __init__.py has its own register().
      The domain name (e.g. "content", "ops") is attached
      to the loaded module info dict for grouping in /help and /start.

Discovery order within each level follows pkgutil.iter_modules() (alphabetical).
"""
import importlib
import pkgutil
import logging

import modules  # the top-level modules package

logger = logging.getLogger(__name__)
_loaded: list[dict] = []


def _register_module(mod_path: str, bot_app, scheduler, authorized_filter,
                     domain: str | None) -> None:
    """Import a module by dotted path, call register(), and store the result."""
    try:
        mod = importlib.import_module(mod_path)
        if not hasattr(mod, "register"):
            logger.warning("Module %s has no register() — skipping", mod_path)
            return
        info: dict = mod.register(bot_app, scheduler, authorized_filter)
        # Attach domain label (None for legacy flat modules).
        info["domain"] = domain
        _loaded.append(info)
        label = f"{domain}/{info['name']}" if domain else info["name"]
        logger.info("✅ Loaded module: %s", label)
    except Exception as e:
        logger.error("Failed to load module %s: %s", mod_path, e, exc_info=True)


def load_modules(bot_app, scheduler, authorized_filter) -> None:
    """
    Scan modules/ for flat modules and domain/submodule groups.

    Logic per entry in modules/:
      - Has register()  → flat legacy module, load directly (domain=None).
      - No register()   → domain namespace, scan one level deeper and load
                          each submodule with domain=<folder name>.
    """
    for _finder, name, ispkg in pkgutil.iter_modules(modules.__path__):
        if not ispkg:
            continue

        top_path = f"modules.{name}"
        top_mod = importlib.import_module(top_path)

        if hasattr(top_mod, "register"):
            # ── Flat module (legacy / backward-compatible) ────────────────
            _register_module(top_path, bot_app, scheduler, authorized_filter,
                             domain=None)
        else:
            # ── Domain namespace — iterate its direct subpackages ─────────
            for _finder2, subname, ispkg2 in pkgutil.iter_modules(top_mod.__path__):
                if not ispkg2:
                    continue
                _register_module(
                    f"{top_path}.{subname}",
                    bot_app, scheduler, authorized_filter,
                    domain=name,
                )


def get_loaded() -> list[dict]:
    return _loaded
