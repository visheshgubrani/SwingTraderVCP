"""Compact, deterministic OHLCV formatting shared by blind vision requests."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _date_text(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def compact_ohlcv_table(candles: Sequence[Any]) -> str:
    """Return the canonical prompt table used by the VCP vision surfaces."""
    lines = ["Date,O,H,L,C,Vol,Vol/50MA"]
    window: list[int] = []
    for candle in candles:
        if len(window) == 50:
            window.pop(0)
        volume = int(candle.volume)
        average = sum(window) / len(window) if window else 0.0
        ratio = round(volume / average, 2) if average > 0 else 1.0
        lines.append(
            f"{_date_text(candle.date)},{float(candle.open):.2f},"
            f"{float(candle.high):.2f},{float(candle.low):.2f},"
            f"{float(candle.close):.2f},{volume},{ratio}"
        )
        window.append(volume)
    return "\n".join(lines)
