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

  Plugin packages (installed via /system install):
      Any pip package that registers an entry point in the group
      "automation.modules" is loaded automatically on startup.
      Entry point value format: "<importable.path>:setup"
      where setup(bot_app, scheduler, authorized_filter) -> dict.

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
        # Auto-import models.py so SQLAlchemy tables register with Base
        # before init_db() is called. Modules without models.py are skipped.
        try:
            importlib.import_module(f"{mod_path}.models")
        except ModuleNotFoundError:
            pass

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


def _load_plugin_entrypoints(bot_app, scheduler, authorized_filter) -> None:
    """
    Discover and load any installed pip packages that register themselves
    under the 'automation.modules' entry-point group.

    Entry point format in pyproject.toml:
        [project.entry-points."automation.modules"]
        finance = "automation_finance.main:setup"

    The callable must have the same signature as register():
        setup(bot_app, scheduler, authorized_filter) -> dict
    """
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="automation.modules")
    except Exception as e:
        logger.warning("Could not read entry points: %s", e)
        return

    for ep in eps:
        try:
            setup_fn = ep.load()
            info: dict = setup_fn(bot_app, scheduler, authorized_filter)
            # Domain may be returned by the plugin or inferred from ep.name
            if "domain" not in info:
                info["domain"] = ep.name
            _loaded.append(info)
            logger.info("✅ Loaded plugin: %s (entry-point)", ep.name)
        except Exception as e:
            logger.error("Failed to load plugin %s: %s", ep.name, e, exc_info=True)


def load_modules(bot_app, scheduler, authorized_filter) -> None:
    """
    Scan modules/ for flat modules and domain/submodule groups,
    then also discover installed plugin packages via entry points.

    Logic per entry in modules/:\n      - Has register()  → flat legacy module, load directly (domain=None).
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

    # ── Plugin packages installed via /system install ─────────────────────────
    _load_plugin_entrypoints(bot_app, scheduler, authorized_filter)


def get_loaded() -> list[dict]:
    return _loaded

