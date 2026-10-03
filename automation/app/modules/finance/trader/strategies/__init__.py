"""Strategy sub-package for the finance/trader module.

All strategies are auto-discovered at import time.
To add a new strategy: create a .py file in this folder, subclass BaseStrategy,
and it will appear automatically in the scanner and manage.py — no other changes needed.
"""
from __future__ import annotations

import importlib
import pkgutil
import logging
from typing import TYPE_CHECKING

from .base import BaseStrategy

logger = logging.getLogger(__name__)

_DISCOVERED: list[BaseStrategy] | None = None


def get_all_strategies() -> list[BaseStrategy]:
    """Return all discovered strategy instances, in registration order."""
    global _DISCOVERED
    if _DISCOVERED is None:
        _DISCOVERED = _discover()
    return _DISCOVERED


def _discover() -> list[BaseStrategy]:
    """Scan this package for BaseStrategy subclasses and instantiate them."""
    found: list[BaseStrategy] = []
    pkg = __name__
    for _importer, modname, _ispkg in pkgutil.walk_packages(
        path=__path__,
        prefix=pkg + ".",
        onerror=lambda x: None,
    ):
        if modname.endswith(".base"):
            continue
        try:
            mod = importlib.import_module(modname)
        except Exception as exc:
            logger.warning("[trader.strategies] Could not import %s: %s", modname, exc)
            continue
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseStrategy)
                and obj is not BaseStrategy
                and not getattr(obj, "_abstract", False)
            ):
                found.append(obj())
    # Sort by declared priority (lower = first)
    found.sort(key=lambda s: s.priority)
    logger.debug("[trader.strategies] Discovered %d strategies: %s",
                 len(found), [s.name for s in found])
    return found
