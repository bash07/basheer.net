"""Chart generator for the finance/trader module.

Produces an annotated candlestick chart for a TradeSetup and saves it to a
temp PNG file.  The chart is designed to be sent directly to Telegram so the
human can visually approve or dismiss the signal.

Usage:
    from modules.finance.trader.chart import generate_chart
    png_path = generate_chart(setup, df_ltf)
    # Returns absolute path to a temporary .png file.
    # Caller is responsible for deleting it after it has been sent.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

# Number of LTF bars to include in the chart window
_CHART_BARS = 80


def generate_chart(setup, df_ltf) -> Optional[str]:
    """Generate an annotated candlestick chart for a TradeSetup.

    Args:
        setup:   TradeSetup object from schemas.py
        df_ltf:  LTF OHLCV DataFrame (DatetimeIndex, Open/High/Low/Close/Volume)

    Returns:
        Absolute path to the saved .png file, or None if chart generation fails.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")   # non-interactive backend — safe for server use
        import matplotlib.patches as mpatches
        import matplotlib.pyplot as plt
        import mplfinance as mpf
        import pandas as pd
    except ImportError as e:
        logger.error("[trader.chart] Missing dependency: %s", e)
        return None

    try:
        # ── Slice last N bars ────────────────────────────────────────────────
        df = df_ltf.copy()

        # mplfinance requires a proper DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)

        # Strip timezone for mplfinance compatibility
        if hasattr(df.index, "tz") and df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        # Use last _CHART_BARS bars
        df = df.tail(_CHART_BARS)
        if len(df) < 10:
            logger.warning("[trader.chart] Too few bars (%d) to plot.", len(df))
            return None

        # ── Build horizontal line annotations ────────────────────────────────
        entry_mid = setup.entry_mid
        sl        = setup.stop_loss
        tp1       = setup.tp1

        # Colours
        COLOR_ENTRY = "#3b82f6"   # blue
        COLOR_SL    = "#ef4444"   # red
        COLOR_TP    = "#22c55e"   # green
        COLOR_ZONE  = "#3b82f6"   # entry zone fill (semi-transparent)

        hlines_vals   = [entry_mid, sl, tp1]
        hlines_colors = [COLOR_ENTRY, COLOR_SL, COLOR_TP]
        hlines_styles = ["--", "-", "-"]
        hlines_widths = [1.2, 1.5, 1.5]

        hline_cfg = dict(
            hlines=hlines_vals,
            colors=hlines_colors,
            linestyle=hlines_styles,
            linewidths=hlines_widths,
        )

        # ── Custom style ─────────────────────────────────────────────────────
        style = mpf.make_mpf_style(
            base_mpf_style="nightclouds",
            facecolor="#0f172a",
            edgecolor="#1e293b",
            figcolor="#0f172a",
            gridcolor="#1e293b",
            gridstyle="--",
            rc={
                "axes.labelcolor": "#94a3b8",
                "xtick.color":     "#94a3b8",
                "ytick.color":     "#94a3b8",
                "font.size":       9,
            },
        )

        # ── Build title ──────────────────────────────────────────────────────
        dir_arrow = "▲ LONG" if setup.direction == "long" else "▼ SHORT"
        conf_tag  = setup.confluence.label.upper()
        title = (
            f"{setup.ticker} · {setup.setup_name} · {dir_arrow}  "
            f"[{conf_tag}  {setup.confluence.score:.1f}/10]  "
            f"R:R {setup.rr_ratio:.1f}×"
        )

        # ── Save to a temp file ──────────────────────────────────────────────
        tmp = tempfile.NamedTemporaryFile(
            suffix=".png",
            prefix=f"trader_{setup.ticker}_",
            delete=False,
        )
        tmp.close()
        png_path = tmp.name

        # ── Plot ─────────────────────────────────────────────────────────────
        fig, axes = mpf.plot(
            df,
            type="candle",
            style=style,
            title=title,
            ylabel="Price",
            volume=True,
            volume_alpha=0.4,
            figsize=(12, 7),
            returnfig=True,
            **hline_cfg,
        )

        ax = axes[0]   # main price axis

        # ── Shade entry zone (entry_low to entry_high) ────────────────────────
        zone_low  = setup.entry_low
        zone_high = setup.entry_high
        ax.axhspan(
            zone_low, zone_high,
            alpha=0.15,
            color=COLOR_ZONE,
            label="Entry Zone",
            zorder=0,
        )

        # ── Right-side labels ─────────────────────────────────────────────────
        x_max = len(df) - 0.5
        label_kwargs = dict(fontsize=8, fontweight="bold")

        ax.text(x_max, tp1,       f"  TP1  {tp1:,.2f}",  color=COLOR_TP,    va="center", **label_kwargs)
        ax.text(x_max, entry_mid, f"  Entry {entry_mid:,.2f}", color=COLOR_ENTRY, va="center", **label_kwargs)
        ax.text(x_max, sl,        f"  SL   {sl:,.2f}",   color=COLOR_SL,    va="center", **label_kwargs)

        # ── Legend patch ──────────────────────────────────────────────────────
        legend_patches = [
            mpatches.Patch(color=COLOR_ENTRY, alpha=0.4, label="Entry Zone"),
            mpatches.Patch(color=COLOR_TP,    label=f"TP1  (+{abs(tp1 - entry_mid) / entry_mid * 100:.2f}%)"),
            mpatches.Patch(color=COLOR_SL,    label=f"SL   (-{abs(sl - entry_mid) / entry_mid * 100:.2f}%)"),
        ]
        ax.legend(
            handles=legend_patches,
            loc="upper left",
            facecolor="#1e293b",
            edgecolor="#334155",
            labelcolor="#cbd5e1",
            fontsize=8,
        )

        fig.savefig(png_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)

        logger.info("[trader.chart] Chart saved: %s", png_path)
        return png_path

    except Exception as e:
        logger.error("[trader.chart] Chart generation failed: %s", e, exc_info=True)
        return None
