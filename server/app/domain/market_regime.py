"""Market regime classification at journal entry time."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

CLASSIFIER_VERSION = "nifty_regime_v1"
BENCHMARK_SYMBOL = "NSE:NIFTY50-INDEX"

Regime = Literal["bullish", "bearish", "neutral", "unavailable"]
PriceSource = Literal["eod_close", "live_ltp"]


@dataclass(frozen=True)
class RegimeEvidence:
    reference_eod_date: str | None
    benchmark_price: Decimal | None
    benchmark_price_source: PriceSource | str
    sma_50: Decimal | None
    sma_200: Decimal | None
    sma_50_slope_20d: Decimal | None
    breadth_above_sma_50_pct: Decimal | None
    breadth_above_sma_200_pct: Decimal | None
    price_above_sma_50: bool | None
    sma_50_above_sma_200: bool | None
    sma_50_rising: bool | None
    stale: bool
    insufficient_data: bool


def _pct_above(count: int, total: int) -> Decimal | None:
    if total <= 0:
        return None
    return Decimal(count * 100) / Decimal(total)


def classify_regime(
    *,
    benchmark_price: Decimal | None,
    sma_50: Decimal | None,
    sma_200: Decimal | None,
    sma_50_slope_20d: Decimal | None,
    constituents_above_sma_50: int,
    constituents_total: int,
    stale: bool = False,
    insufficient_data: bool = False,
) -> tuple[Regime, RegimeEvidence]:
    breadth_50 = _pct_above(constituents_above_sma_50, constituents_total)

    evidence = RegimeEvidence(
        reference_eod_date=None,
        benchmark_price=benchmark_price,
        benchmark_price_source="eod_close",
        sma_50=sma_50,
        sma_200=sma_200,
        sma_50_slope_20d=sma_50_slope_20d,
        breadth_above_sma_50_pct=breadth_50,
        breadth_above_sma_200_pct=None,
        price_above_sma_50=(
            benchmark_price > sma_50
            if benchmark_price is not None and sma_50 is not None
            else None
        ),
        sma_50_above_sma_200=(
            sma_50 > sma_200
            if sma_50 is not None and sma_200 is not None
            else None
        ),
        sma_50_rising=(
            sma_50_slope_20d > 0 if sma_50_slope_20d is not None else None
        ),
        stale=stale,
        insufficient_data=insufficient_data,
    )

    if (
        stale
        or insufficient_data
        or benchmark_price is None
        or sma_50 is None
        or sma_200 is None
        or sma_50_slope_20d is None
        or breadth_50 is None
    ):
        return "unavailable", evidence

    bullish_trend = (
        benchmark_price > sma_50
        and sma_50 > sma_200
        and sma_50_slope_20d > 0
        and breadth_50 >= Decimal("55")
    )
    bearish_trend = (
        benchmark_price < sma_50
        and sma_50 < sma_200
        and sma_50_slope_20d < 0
        and breadth_50 <= Decimal("45")
    )

    if bullish_trend:
        return "bullish", evidence
    if bearish_trend:
        return "bearish", evidence
    return "neutral", evidence


def is_stale_reference(reference_eod_date, as_of_date, *, max_age_days: int = 3) -> bool:
    if reference_eod_date is None or as_of_date is None:
        return True
    return (as_of_date - reference_eod_date).days > max_age_days
