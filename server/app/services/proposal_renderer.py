"""Headless Proposal Chart Renderer.

Generates standardized, deterministic 1280x720 PNG charts using pinned matplotlib/mplfinance (Agg backend):
1. Raw 252-session context chart for humans (not sent to Gemini).
2. Clean 126-session LLM chart (log price, EMA21, SMA50/150/200, volume; no overlays).

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

from app.domain.p10_geometry import CandleData


RENDERER_VERSION = "p10_mplfinance_v4"
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


def _moving_averages(df: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "ema21": df["Close"].ewm(span=21, adjust=False).mean(),
        "sma50": df["Close"].rolling(50).mean(),
        "sma150": df["Close"].rolling(150).mean(),
        "sma200": df["Close"].rolling(200).mean(),
    }


def render_context_chart(df_252: pd.DataFrame, symbol: str) -> bytes:
    """Renders 252-session raw context chart with EMA21, SMA50, SMA150, SMA200, log scale, and volume."""
    df = df_252.copy()
    averages = _moving_averages(df)
    plots = [
        mpf.make_addplot(averages["ema21"], color="#00e5ff", width=1.2),
        mpf.make_addplot(averages["sma50"], color="#ffab00", width=1.2),
        mpf.make_addplot(averages["sma150"], color="#d500f9", width=1.2),
        mpf.make_addplot(averages["sma200"], color="#ff1744", width=1.4),
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
    averages: dict[str, pd.Series] | None = None,
) -> bytes:
    """Render the clean 126-session LLM chart. No contraction overlays.

    Moving averages should be computed on the 252-session freeze and sliced
    to the visible 126 sessions so SMA150/200 are defined.
    """
    df = df_126.copy()
    if averages is None:
        averages = _moving_averages(df)
    plots = [
        mpf.make_addplot(averages["ema21"].reindex(df.index), color="#00e5ff", width=1.2),
        mpf.make_addplot(averages["sma50"].reindex(df.index), color="#ffab00", width=1.2),
        mpf.make_addplot(averages["sma150"].reindex(df.index), color="#d500f9", width=1.2),
        mpf.make_addplot(averages["sma200"].reindex(df.index), color="#ff1744", width=1.4),
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
        title=f"{symbol} — 126-Session VCP Window (Log Scale)",
    )
    del axes
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
    """Generate the 252-session human context chart and clean 126-session LLM chart.

    ``pivot_price``, ``stop_price``, and ``contraction_anchors`` remain accepted
    for call-site compatibility but are never drawn on the model-facing image.
    """
    del pivot_price, stop_price, contraction_anchors
    df = candles_to_dataframe(candles)

    df_252 = df.tail(252) if len(df) >= 252 else df
    context_png = render_context_chart(df_252, symbol)
    context_hash = hashlib.sha256(context_png).hexdigest()

    averages = _moving_averages(df_252)
    df_126 = df_252.tail(126) if len(df_252) >= 126 else df_252
    detail_png = render_detail_chart(df_126=df_126, symbol=symbol, averages=averages)
    detail_hash = hashlib.sha256(detail_png).hexdigest()

    return RenderedProposalCharts(
        renderer_version=RENDERER_VERSION,
        context_png=context_png,
        context_hash=context_hash,
        detail_png=detail_png,
        detail_hash=detail_hash,
    )
