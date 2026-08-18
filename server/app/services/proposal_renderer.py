"""Headless Proposal Chart Renderer.

Generates standardized, deterministic 1280x720 PNG charts using pinned matplotlib/mplfinance (Agg backend):
1. Raw 252-session context chart (log scale, EMA21, SMA50/150/200, volume pane).
2. Deterministically annotated 126-session detail chart (log scale, contraction geometry, pivot line).

Strictly adheres to AGENTS.md §5.1.
"""

from __future__ import annotations

import hashlib
import io
import datetime as dt
import os
from dataclasses import dataclass
from typing import Sequence

os.environ.setdefault("MPLCONFIGDIR", "/tmp/swingtradervcp-matplotlib")

import matplotlib
matplotlib.use("Agg")  # Non-interactive headless backend
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd

from app.domain.p10_geometry import CandleData, ChartGeometryAnchor


RENDERER_VERSION = "p10_mplfinance_v3"
CHART_WIDTH = 1280
CHART_HEIGHT = 720
CHART_DPI = 100

matplotlib.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.unicode_minus": False,
        "path.simplify": False,
        "savefig.dpi": CHART_DPI,
    }
)


@dataclass(frozen=True)
class RenderedProposalCharts:
    renderer_version: str
    context_png: bytes
    context_hash: str
    detail_png: bytes
    detail_hash: str


def candles_to_dataframe(candles: Sequence[CandleData]) -> pd.DataFrame:
    """Converts a sequence of CandleData into an mplfinance-compatible DataFrame."""
    records = []
    for i, c in enumerate(candles):
        date_val = c.date or (dt.date(2000, 1, 1) + dt.timedelta(days=i)).isoformat()
        records.append({
            "Date": pd.to_datetime(date_val),
            "Open": float(c.open),
            "High": float(c.high),
            "Low": float(c.low),
            "Close": float(c.close),
            "Volume": int(c.volume),
        })
    df = pd.DataFrame(records)
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    return df


def render_context_chart(df_252: pd.DataFrame, symbol: str) -> bytes:
    """Renders 252-session raw context chart with EMA21, SMA50, SMA150, SMA200, log scale, and volume."""
    df = df_252.copy()
    
    # Calculate indicators
    ema21 = df["Close"].ewm(span=21, adjust=False).mean()
    sma50 = df["Close"].rolling(50).mean()
    sma150 = df["Close"].rolling(150).mean()
    sma200 = df["Close"].rolling(200).mean()

    plots = [
        mpf.make_addplot(ema21, color="#00e5ff", width=1.2),    # Cyan EMA21
        mpf.make_addplot(sma50, color="#ffab00", width=1.2),    # Amber SMA50
        mpf.make_addplot(sma150, color="#d500f9", width=1.2),   # Magenta SMA150
        mpf.make_addplot(sma200, color="#ff1744", width=1.4),   # Red SMA200
    ]

    custom_style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#00e676",
            down="#ff5252",
            edge="inherit",
            wick="inherit",
            volume={"up": "#00e676", "down": "#ff5252"},
        ),
        facecolor="#131722",
        figcolor="#0a0e17",
        gridcolor="#1e222d",
        gridstyle="--",
    )

    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=custom_style,
        addplot=plots,
        volume=True,
        yscale="log",
        figsize=(CHART_WIDTH / CHART_DPI, CHART_HEIGHT / CHART_DPI),
        returnfig=True,
        panel_ratios=(4, 1),
        tight_layout=True,
        title=f"{symbol} — 252-Session Context (Log Scale)",
    )
    fig.savefig(
        buf,
        format="png",
        dpi=CHART_DPI,
        metadata={"Software": RENDERER_VERSION},
    )
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_detail_chart(
    df_126: pd.DataFrame,
    symbol: str,
    pivot_price: float | None = None,
    stop_price: float | None = None,
    contraction_anchors: Sequence[ChartGeometryAnchor] = (),
) -> bytes:
    """Render the detail chart with deterministic geometry and pivot only.

    ``stop_price`` remains accepted for call-site compatibility, but the
    structural stop is deliberately never drawn in the model-facing image.
    """
    del stop_price
    df = df_126.copy()
    ema21 = df["Close"].ewm(span=21, adjust=False).mean()
    sma50 = df["Close"].rolling(50).mean()

    plots = [
        mpf.make_addplot(ema21, color="#00e5ff", width=1.2),
        mpf.make_addplot(sma50, color="#ffab00", width=1.2),
    ]

    custom_style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up="#00e676",
            down="#ff5252",
            edge="inherit",
            wick="inherit",
            volume={"up": "#00e676", "down": "#ff5252"},
        ),
        facecolor="#131722",
        figcolor="#0a0e17",
        gridcolor="#1e222d",
        gridstyle="--",
    )

    hlines_kwargs = {}
    if pivot_price is not None:
        hlines_kwargs = {
            "hlines": dict(
                hlines=[pivot_price],
                colors=["#00e676"],
                linestyle=["-."],
                linewidths=[1.5],
            )
        }

    buf = io.BytesIO()
    fig, axes = mpf.plot(
        df,
        type="candle",
        style=custom_style,
        addplot=plots,
        volume=True,
        yscale="log",
        figsize=(CHART_WIDTH / CHART_DPI, CHART_HEIGHT / CHART_DPI),
        returnfig=True,
        panel_ratios=(4, 1),
        tight_layout=True,
        title=f"{symbol} — 126-Session VCP Detail (Log Scale)",
        **hlines_kwargs,
    )

    if pivot_price is not None:
        ax = axes[0]
        ax.text(
            0.02,
            0.95,
            f"Pivot: {pivot_price:.2f}",
            transform=ax.transAxes,
            color="#ffffff",
            fontsize=10,
            verticalalignment="top",
            bbox=dict(boxstyle="round", facecolor="#1e222d", alpha=0.8),
        )

    if contraction_anchors:
        ax = axes[0]
        index_positions = {
            timestamp.date().isoformat(): index
            for index, timestamp in enumerate(df.index)
        }
        colors = {
            "contraction_high": "#ffab00",
            "contraction_low": "#00e5ff",
            "resistance": "#00e676",
        }
        markers = {
            "contraction_high": "v",
            "contraction_low": "^",
            "resistance": "D",
        }
        for anchor in contraction_anchors:
            x_position = index_positions.get(anchor.date)
            if x_position is None:
                continue
            ax.scatter(
                [x_position],
                [float(anchor.price)],
                color=colors.get(anchor.anchor_type, "#ffffff"),
                marker=markers.get(anchor.anchor_type, "o"),
                s=34,
                zorder=5,
            )

    fig.savefig(
        buf,
        format="png",
        dpi=CHART_DPI,
        metadata={"Software": RENDERER_VERSION},
    )
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_proposal_charts(
    candles: Sequence[CandleData],
    symbol: str,
    pivot_price: float | None = None,
    stop_price: float | None = None,
    contraction_anchors: Sequence[ChartGeometryAnchor] = (),
) -> RenderedProposalCharts:
    """Generates both 252-session context and 126-session detail PNGs and returns SHA256 hashes."""
    df = candles_to_dataframe(candles)
    
    # 252 context
    df_252 = df.tail(252) if len(df) >= 252 else df
    context_png = render_context_chart(df_252, symbol)
    context_hash = hashlib.sha256(context_png).hexdigest()

    # 126 detail
    df_126 = df.tail(126) if len(df) >= 126 else df
    detail_png = render_detail_chart(
        df_126=df_126,
        symbol=symbol,
        pivot_price=pivot_price,
        stop_price=stop_price,
        contraction_anchors=contraction_anchors,
    )
    detail_hash = hashlib.sha256(detail_png).hexdigest()

    return RenderedProposalCharts(
        renderer_version=RENDERER_VERSION,
        context_png=context_png,
        context_hash=context_hash,
        detail_png=detail_png,
        detail_hash=detail_hash,
    )
