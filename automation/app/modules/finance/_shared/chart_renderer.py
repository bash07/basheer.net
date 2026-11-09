"""Memory-safe financial chart renderer for the e2-micro (1 GB RAM) instance.

Strategy: matplotlib, mplfinance, pandas, and numpy are imported only inside a
short-lived subprocess. When the subprocess exits, ALL of that memory is
released — keeping the parent FastAPI process's baseline footprint low.

The parent process serialises chart parameters to JSON, passes them to the
subprocess via stdin, and reads the resulting PNG file path from the filesystem.

Usage:
    from modules.finance._shared.chart_renderer import render_chart

    png_path = await render_chart({
        "ohlcv_json": df.to_json(orient="split"),
        "emas":       {"ema21": [...], "ema50": [...], "ema200": [...]},
        "rsi":        [...],          # optional RSI values
        "title":      "AAPL — Daily",
    })
    # Send png_path to Telegram, then os.unlink(png_path) to clean up.
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
import textwrap
from typing import Any

logger = logging.getLogger(__name__)

# ── Subprocess render script ──────────────────────────────────────────────────
# Runs in a fresh Python process — heavy imports (matplotlib, mplfinance,
# pandas) only exist inside this subprocess, not in the parent.
_RENDER_SCRIPT = textwrap.dedent(r"""
import io, json, sys, os
params = json.loads(sys.stdin.read())
output_path = params["output_path"]

import pandas as pd
import mplfinance as mpf
import matplotlib
matplotlib.use("Agg")  # headless — no display needed
import matplotlib.pyplot as plt

# ── Reconstruct OHLCV DataFrame ──────────────────────────────────────────────
# Wrap in StringIO — pandas 2.x on Linux treats a bare string as a file path,
# causing OSError(ENAMETOOLONG) when the JSON payload is large.
df = pd.read_json(io.StringIO(params["ohlcv_json"]), orient="split")
df.index = pd.to_datetime(df.index)
df.index.name = "Date"

# Ensure required columns exist (yfinance capitalises them)
df.columns = [c.title() for c in df.columns]

# ── EMA overlay add-plots ─────────────────────────────────────────────────────
EMA_COLORS = {
    "ema21":  "#00bcd4",   # cyan
    "ema50":  "#ff9800",   # amber
    "ema100": "#9c27b0",   # purple
    "ema200": "#f44336",   # red
}
addplots = []
for name, values in params.get("emas", {}).items():
    if len(values) != len(df):
        continue
    series = pd.Series(values, index=df.index)
    addplots.append(
        mpf.make_addplot(series, color=EMA_COLORS.get(name, "#ffffff"), width=1.2)
    )

# ── RSI sub-panel ─────────────────────────────────────────────────────────────
has_rsi = "rsi" in params and len(params["rsi"]) == len(df)
if has_rsi:
    rsi = pd.Series(params["rsi"], index=df.index)
    addplots.extend([
        mpf.make_addplot(rsi, panel=2, color="#00e676", ylabel="RSI(14)"),
        mpf.make_addplot(
            pd.Series(70, index=df.index), panel=2,
            color="#ef5350", linestyle="--", width=0.8
        ),
        mpf.make_addplot(
            pd.Series(30, index=df.index), panel=2,
            color="#26c6da", linestyle="--", width=0.8
        ),
    ])

# ── Volume Profile Overlays (POC, VAH, VAL) ──────────────────────────────────
vp = params.get("volume_profile", {})
if vp and "poc" in vp and "vah" in vp and "val" in vp:
    poc_val = float(vp["poc"])
    vah_val = float(vp["vah"])
    val_val = float(vp["val"])
    # POC (Gold solid line)
    addplots.append(
        mpf.make_addplot(pd.Series(poc_val, index=df.index), color="#ffd700", linestyle="-", width=1.4)
    )
    # VAH (Light blue dashed)
    addplots.append(
        mpf.make_addplot(pd.Series(vah_val, index=df.index), color="#64b5f6", linestyle="--", width=1.0)
    )
    # VAL (Light purple dashed)
    addplots.append(
        mpf.make_addplot(pd.Series(val_val, index=df.index), color="#ba68c8", linestyle="--", width=1.0)
    )

# ── Wyckoff Overlays ──────────────────────────────────────────────────────────
wy = params.get("wyckoff", {})
if wy:
    if wy.get("range_high"):
        addplots.append(
            mpf.make_addplot(pd.Series(float(wy["range_high"]), index=df.index), color="#f59e0b", linestyle="--", width=1.2)
        )
    if wy.get("range_low"):
        addplots.append(
            mpf.make_addplot(pd.Series(float(wy["range_low"]), index=df.index), color="#f59e0b", linestyle="--", width=1.2)
        )
    if wy.get("t_cons") and float(wy["t_cons"]) > 0:
        addplots.append(
            mpf.make_addplot(pd.Series(float(wy["t_cons"]), index=df.index), color="#10b981", linestyle=":", width=1.4)
        )
    if wy.get("t_full") and float(wy["t_full"]) > 0:
        addplots.append(
            mpf.make_addplot(pd.Series(float(wy["t_full"]), index=df.index), color="#34d399", linestyle=":", width=1.4)
        )

# ── Style ─────────────────────────────────────────────────────────────────────
mc = mpf.make_marketcolors(
    up="#26a69a", down="#ef5350",
    edge="inherit", wick="inherit", volume="in",
)
style = mpf.make_mpf_style(
    base_mpf_style="nightclouds",
    marketcolors=mc,
    gridstyle=":",
)

# ── Plot & save ───────────────────────────────────────────────────────────────
panel_ratios = (4, 1, 2) if has_rsi else (4, 1)
mpf.plot(
    df,
    type="candle",
    style=style,
    title=params.get("title", ""),
    volume=True,
    addplot=addplots or None,
    panel_ratios=panel_ratios,
    figsize=(12, 8),
    savefig=dict(fname=output_path, dpi=130, bbox_inches="tight"),
    returnfig=False,
)
plt.close("all")
""").strip()


# ── Public API ────────────────────────────────────────────────────────────────

async def render_chart(params: dict[str, Any]) -> str:
    """
    Render a financial candlestick chart in a subprocess and return the PNG path.

    Args:
        params: Dict with keys:
            ohlcv_json (str)  — DataFrame serialised via df.to_json(orient="split")
            emas       (dict) — {name: [float, ...]} aligned to OHLCV index
            rsi        (list) — RSI values aligned to OHLCV index (optional)
            title      (str)  — Chart title shown at the top
            output_path (str) — Where to write the PNG (optional; temp file used if absent)

    Returns:
        Absolute path to the generated PNG file.

    Raises:
        RuntimeError: If the subprocess exits non-zero.
    """
    if "output_path" not in params:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="fin_chart_")
        os.close(fd)
        params = {**params, "output_path": path}
    else:
        path = params["output_path"]

    payload = json.dumps(params).encode()

    _RENDER_TIMEOUT = 60  # seconds — cold-start of matplotlib/mplfinance can be slow

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", _RENDER_SCRIPT,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(input=payload), timeout=_RENDER_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.error("Chart renderer subprocess timed out after %ds", _RENDER_TIMEOUT)
        raise RuntimeError(f"Chart rendering timed out after {_RENDER_TIMEOUT}s")

    if proc.returncode != 0:
        err = stderr.decode(errors="replace")[:600]
        logger.error("Chart renderer subprocess failed (rc=%d): %s", proc.returncode, err)
        raise RuntimeError(f"Chart rendering failed: {err}")

    logger.debug("Chart rendered → %s", path)
    return path


def cleanup_chart(path: str) -> None:
    """Delete a temporary chart PNG after it has been sent to Telegram."""
    try:
        os.unlink(path)
        logger.debug("Chart deleted: %s", path)
    except OSError:
        pass
