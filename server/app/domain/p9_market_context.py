"""Pure deterministic P9 market/sector classification and selection policy."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Literal, Mapping, Sequence


MarketLight = Literal["green", "yellow", "red", "unavailable"]
SectorTier = Literal["leading", "neutral", "lagging", "unavailable"]

POLICY_VERSION = "market_context_v1"
GREEN_MULTIPLIER = Decimal("1.00")
YELLOW_MULTIPLIER = Decimal("0.50")
RED_MULTIPLIER = Decimal("0.00")


@dataclass(frozen=True)
class SectorStrength:
    sector_code: str
    score: Decimal
    ordinal_rank: int
    rs_rating: int
    raw_tier: SectorTier
    gate_tier: SectorTier


def classify_index_trend(
    *,
    close: Decimal | None,
    sma50: Decimal | None,
    sma200: Decimal | None,
    sma200_20_sessions_ago: Decimal | None,
) -> MarketLight:
    if None in (close, sma50, sma200, sma200_20_sessions_ago):
        return "unavailable"
    assert close is not None and sma50 is not None
    assert sma200 is not None and sma200_20_sessions_ago is not None
    if close > sma50 and close > sma200 and sma200 >= sma200_20_sessions_ago:
        return "green"
    if close < sma50 and close < sma200 and sma200 < sma200_20_sessions_ago:
        return "red"
    return "yellow"


def majority_light(states: Iterable[MarketLight]) -> MarketLight:
    values = list(states)
    if not values or "unavailable" in values:
        return "unavailable"
    if values.count("green") >= 2:
        return "green"
    if values.count("red") >= 2:
        return "red"
    return "yellow"


def classify_breadth(percent_above_sma50: Decimal | None) -> MarketLight:
    if percent_above_sma50 is None:
        return "unavailable"
    if percent_above_sma50 > Decimal("50"):
        return "green"
    if percent_above_sma50 < Decimal("25"):
        return "red"
    return "yellow"


def typical_turnover(
    *, high: Decimal, low: Decimal, close: Decimal, volume: int
) -> Decimal:
    if volume < 0:
        raise ValueError("volume cannot be negative")
    return ((high + low + close) / Decimal("3")) * Decimal(volume)


def is_distribution_session(
    *,
    current_close: Decimal,
    previous_close: Decimal,
    current_turnover: Decimal,
    previous_turnover: Decimal,
) -> bool:
    if previous_close <= 0:
        raise ValueError("previous close must be positive")
    return (
        (current_close / previous_close) - Decimal("1") <= Decimal("-0.005")
        and current_turnover > previous_turnover
    )


def classify_distribution_count(count: int | None) -> MarketLight:
    if count is None or count < 0:
        return "unavailable"
    if count <= 3:
        return "green"
    if count <= 5:
        return "yellow"
    return "red"


def classify_market_light(
    *, trend: MarketLight, breadth: MarketLight, distribution: MarketLight
) -> MarketLight:
    return majority_light((trend, breadth, distribution))


def exposure_multiplier(light: MarketLight) -> Decimal:
    return {
        "green": GREEN_MULTIPLIER,
        "yellow": YELLOW_MULTIPLIER,
        "red": RED_MULTIPLIER,
        "unavailable": RED_MULTIPLIER,
    }[light]


def excess_return(
    *, current: Decimal, historic: Decimal, benchmark_current: Decimal, benchmark_historic: Decimal
) -> Decimal:
    if historic <= 0 or benchmark_historic <= 0:
        raise ValueError("historic prices must be positive")
    return (current / historic - Decimal("1")) - (
        benchmark_current / benchmark_historic - Decimal("1")
    )


def blended_sector_score(
    *, excess_short: Decimal, excess_long: Decimal, short_weight: Decimal = Decimal("0.60")
) -> Decimal:
    if short_weight < 0 or short_weight > 1:
        raise ValueError("short_weight must be between zero and one")
    return short_weight * excess_short + (Decimal("1") - short_weight) * excess_long


def rank_sector_strength(
    scores: Mapping[str, Decimal],
    *,
    previous_raw_tiers: Mapping[str, SectorTier] | None = None,
) -> list[SectorStrength]:
    """Rank sectors without arbitrary symbol tie breaks at tier boundaries."""
    if not scores:
        return []
    previous = previous_raw_tiers or {}
    ordered_groups: list[tuple[Decimal, list[str]]] = []
    for score in sorted(set(scores.values()), reverse=True):
        ordered_groups.append((score, sorted(code for code, value in scores.items() if value == score)))

    total = len(scores)
    tier_size = math.ceil(total * 0.30)
    output: list[SectorStrength] = []
    cursor = 1
    for score, codes in ordered_groups:
        group_start = cursor
        group_end = cursor + len(codes) - 1
        if group_end <= tier_size:
            raw_tier: SectorTier = "leading"
        elif group_start > total - tier_size:
            raw_tier = "lagging"
        else:
            raw_tier = "neutral"
        for code in codes:
            rating = max(1, min(99, int(round((total - group_start + 1) / total * 98) + 1)))
            if raw_tier == "lagging":
                gate_tier: SectorTier = (
                    "lagging" if previous.get(code) == "lagging" else "neutral"
                )
            else:
                # Fast-out: the first non-lagging snapshot releases the gate.
                gate_tier = raw_tier
            output.append(
                SectorStrength(
                    sector_code=code,
                    score=score,
                    ordinal_rank=group_start,
                    rs_rating=rating,
                    raw_tier=raw_tier,
                    gate_tier=gate_tier,
                )
            )
        cursor = group_end + 1
    return sorted(output, key=lambda item: (item.ordinal_rank, item.sector_code))


def contextual_selection_order(
    candidates: Sequence[dict],
    *,
    band_width: Decimal = Decimal("2"),
) -> list[dict]:
    """Return a separate P9 order while preserving each technical result rank."""
    remaining = [dict(candidate) for candidate in candidates]
    ordered: list[dict] = []
    priority = {"leading": 0, "neutral": 1, "lagging": 2, "unavailable": 3}
    while remaining:
        highest = max(Decimal(str(item["technical_score"])) for item in remaining)
        lower = highest - band_width
        band = [item for item in remaining if Decimal(str(item["technical_score"])) >= lower]
        band_ids = {id(item) for item in band}
        remaining = [item for item in remaining if id(item) not in band_ids]
        band.sort(
            key=lambda item: (
                priority.get(str(item.get("sector_tier") or "unavailable"), 3),
                -int(item.get("sector_rs_rating") or 0),
                int(item["result_rank"]),
            )
        )
        ordered.extend(band)
    return [
        {**candidate, "contextual_selection_rank": index}
        for index, candidate in enumerate(ordered, start=1)
    ]

