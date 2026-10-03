"""Interactive setup manager for the Trade trader module.

Config schema — .trader_config.json:
    {
      "setups": {
        "OB Retracement":          false,
        "Liquidity Sweep + FVG":   false,
        "Displacement + FVG":      false,
        "EMA Pullback + Hidden Div": true
      }
    }

Every known setup is listed with an explicit true/false state.
When a new setup is added to the code, it appears in the manager
automatically with a default state of ENABLED.

Usage:
    python -m modules.finance.trader.manage
"""
from __future__ import annotations

import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), ".trader_config.json")


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load raw config dict from JSON. Returns empty dict if missing."""
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: dict) -> None:
    """Persist config dict to JSON (pretty-printed)."""
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def _sync_config(config: dict, all_names: list[str]) -> dict[str, bool]:
    """Return the full setup-state map, adding any new setups as enabled.

    - Existing entries keep their saved state.
    - Setups present in code but missing from config → default to True (enabled).
    - Setups in config that no longer exist in code → silently dropped.
    """
    saved: dict[str, bool] = config.get("setups", {})
    synced: dict[str, bool] = {}
    for name in all_names:
        # Preserve saved state; new setups default to enabled
        synced[name] = bool(saved.get(name, True))
    return synced


def get_disabled_setups() -> set[str]:
    """Return the set of setup names that are currently disabled.

    Called by setups.py at import time and by backtest.py before each run.
    New setups not yet in the config are treated as enabled.
    """
    from modules.finance.trader.setups import ALL_SETUP_NAMES
    config = load_config()
    state  = _sync_config(config, ALL_SETUP_NAMES)
    return {name for name, enabled in state.items() if not enabled}


# ── Interactive Manager ───────────────────────────────────────────────────────

def run_manager() -> None:
    """Number-based toggle menu. Changes are saved to .trader_config.json."""
    from modules.finance.trader.setups import ALL_SETUP_NAMES

    config = load_config()
    state  = _sync_config(config, ALL_SETUP_NAMES)   # name → bool (enabled)

    def _print_menu() -> None:
        enabled_count  = sum(1 for v in state.values() if v)
        disabled_count = sum(1 for v in state.values() if not v)
        print()
        print("=" * 58)
        print("   Trade Setup Manager")
        print(f"   {enabled_count} enabled  /  {disabled_count} disabled")
        print("=" * 58)
        for i, name in enumerate(ALL_SETUP_NAMES, 1):
            on    = state.get(name, True)
            tick  = "v" if on  else "x"
            label = "  ON " if on else " OFF "
            print(f"   [{tick}]  {i}.  [{label}]  {name}")
        print()
        print("   Enter number(s) to toggle  (e.g.  2   or  1,3 or 1-3)")
        print("   Press Enter with no input to save and quit.")
        print()

    while True:
        _print_menu()
        raw = input("   > ").strip()

        if not raw:
            break

        # Parse: comma/space separated, and support ranges like 1-3
        nums: list[int] = []
        for token in raw.replace(",", " ").split():
            if "-" in token and not token.startswith("-"):
                parts = token.split("-")
                try:
                    lo, hi = int(parts[0]), int(parts[1])
                    nums.extend(range(lo, hi + 1))
                except (ValueError, IndexError):
                    print(f"   Invalid range: {token}")
            else:
                try:
                    nums.append(int(token))
                except ValueError:
                    print(f"   Invalid input: {token}")

        changed = []
        for n in nums:
            if 1 <= n <= len(ALL_SETUP_NAMES):
                name = ALL_SETUP_NAMES[n - 1]
                state[name] = not state[name]
                verb = "Enabled " if state[name] else "Disabled"
                changed.append(f"   {verb}: {name}")
            else:
                print(f"   Number {n} out of range (1-{len(ALL_SETUP_NAMES)})")

        if changed:
            print()
            for line in changed:
                print(line)

    # Persist the full state map
    config["setups"] = state
    save_config(config)

    # Final summary
    enabled  = [n for n in ALL_SETUP_NAMES if state.get(n, True)]
    disabled = [n for n in ALL_SETUP_NAMES if not state.get(n, True)]

    print()
    print("=" * 58)
    print("   Saved.")
    print()
    if enabled:
        print(f"   ENABLED  ({len(enabled)}):"),
        for n in enabled:
            print(f"     v  {n}")
    if disabled:
        print()
        print(f"   DISABLED ({len(disabled)}):")
        for n in disabled:
            print(f"     x  {n}")
    print("=" * 58)
    print()


def main() -> None:
    run_manager()


if __name__ == "__main__":
    main()
