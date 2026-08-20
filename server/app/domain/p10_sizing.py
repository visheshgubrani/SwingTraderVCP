"""P10 Sizing, Allocation & Exit Apportionment.

Deterministic Python sizing, whole-share floor rounding, staged exit apportionment,
stop-tightening corridor solver, and risk-reduction exit calculations according to AGENTS.md §5 & §6.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from enum import Enum

from app.domain.p10_geometry import DEFAULT_TICK_SIZE, floor_to_tick


class EntryTemplate(str, Enum):
    SINGLE = "single"
    TWO_LEG = "two_leg"
    TWO_LEG_STAGED = "two_leg_staged"
    THREE_LEG_FRONT = "three_leg_front"
    THREE_LEG_BALANCED = "three_leg_balanced"


TEMPLATE_CONFIG = {
    EntryTemplate.SINGLE: {
        "leg_allocations": [Decimal("1.00")],
        "relative_volume_threshold": Decimal("2.00"),
        "leg_count": 1,
    },
    EntryTemplate.TWO_LEG: {
        "leg_allocations": [Decimal("0.60"), Decimal("0.40")],
        "relative_volume_threshold": Decimal("1.75"),
        "leg_count": 2,
    },
    EntryTemplate.TWO_LEG_STAGED: {
        "leg_allocations": [Decimal("0.50"), Decimal("0.50")],
        "relative_volume_threshold": Decimal("1.50"),
        "leg_count": 2,
    },
    EntryTemplate.THREE_LEG_FRONT: {
        "leg_allocations": [Decimal("0.50"), Decimal("0.30"), Decimal("0.20")],
        "relative_volume_threshold": Decimal("2.00"),
        "leg_count": 3,
    },
    EntryTemplate.THREE_LEG_BALANCED: {
        "leg_allocations": [Decimal("0.40"), Decimal("0.30"), Decimal("0.30")],
        "relative_volume_threshold": Decimal("1.50"),
        "leg_count": 3,
    },
}


@dataclass(frozen=True)
class TargetShareApportionment:
    t1_shares: int
    t2_shares: int
    t3_shares: int
    runner_shares: int
    total_shares: int


@dataclass(frozen=True)
class SizingResult:
    shares: int
    allocated_risk: Decimal
    allocated_notional: Decimal
    is_viable: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class StopTighteningResult:
    can_tighten: bool
    new_stop: Decimal
    residual_risk: Decimal
    corridor_max: Decimal


@dataclass(frozen=True)
class RiskReductionResult:
    exit_shares: int
    remaining_shares: int
    remaining_risk: Decimal
    rounding_residual: Decimal
    is_successful: bool


def calculate_leg_sizing(
    leg_risk_budget: Decimal,
    entry_price: Decimal,
    stop_price: Decimal,
    max_notional_cap: Decimal,
    min_shares_for_position: int = 4,
    min_viability_pct: Decimal = Decimal("0.50"),
    lot_size: int = 1,
    approved_leg_risk_budget: Decimal | None = None,
) -> SizingResult:
    """Calculate entry/add size, flooring to the tradable lot increment.

    ``leg_risk_budget`` is the current cap-constrained headroom.  Viability is
    measured against the original approved leg budget so sizing down cannot
    move its own 50% goalpost.
    """
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        return SizingResult(
            shares=0,
            allocated_risk=Decimal("0"),
            allocated_notional=Decimal("0"),
            is_viable=False,
            rejection_reason=f"Entry price ({entry_price}) must be higher than stop ({stop_price})",
        )

    # Risk-based whole shares (floored)
    risk_shares = int((leg_risk_budget / per_share_risk).to_integral_value(rounding=ROUND_FLOOR))
    # Notional cap whole shares (floored)
    notional_shares = int((max_notional_cap / entry_price).to_integral_value(rounding=ROUND_FLOOR))

    raw_shares = max(0, min(risk_shares, notional_shares))
    shares = (raw_shares // lot_size) * lot_size
    allocated_risk = Decimal(str(shares)) * per_share_risk
    allocated_notional = Decimal(str(shares)) * entry_price

    # Minimum viability check: at least 50% of approved leg-risk allocation AND >= 4 shares
    approved_budget = approved_leg_risk_budget or leg_risk_budget
    min_required_risk = approved_budget * min_viability_pct
    minimum_tradable_shares = max(min_shares_for_position, 4 * lot_size)
    if shares < minimum_tradable_shares:
        return SizingResult(
            shares=shares,
            allocated_risk=allocated_risk,
            allocated_notional=allocated_notional,
            is_viable=False,
            rejection_reason=(
                f"Calculated shares ({shares}) below minimum staged-exit size "
                f"({minimum_tradable_shares}, four tradable lots)"
            ),
        )

    if allocated_risk < min_required_risk:
        return SizingResult(
            shares=shares,
            allocated_risk=allocated_risk,
            allocated_notional=allocated_notional,
            is_viable=False,
            rejection_reason=f"Allocated risk ({allocated_risk:.2f}) below 50% viability threshold ({min_required_risk:.2f})",
        )

    return SizingResult(
        shares=shares,
        allocated_risk=allocated_risk,
        allocated_notional=allocated_notional,
        is_viable=True,
        rejection_reason=None,
    )


def apportion_staged_exits(total_shares: int) -> TargetShareApportionment:
    """Apportions total shares across 4 staged exit buckets (25% / 25% / 25% / 25%)
    using deterministic largest-remainder apportionment.
    """
    if total_shares <= 0:
        return TargetShareApportionment(0, 0, 0, 0, 0)

    base = total_shares // 4
    remainder = total_shares % 4

    # Priority for extra shares: T1, T2, T3, Runner
    t1 = base + (1 if remainder >= 1 else 0)
    t2 = base + (1 if remainder >= 2 else 0)
    t3 = base + (1 if remainder >= 3 else 0)
    runner = base

    assert t1 + t2 + t3 + runner == total_shares, "Apportionment sum mismatch"
    return TargetShareApportionment(
        t1_shares=t1,
        t2_shares=t2,
        t3_shares=t3,
        runner_shares=runner,
        total_shares=total_shares,
    )


def solve_stop_tightening(
    position_shares: int,
    entry_vwap: Decimal,
    current_stop: Decimal,
    base_low: Decimal,
    approved_max_risk: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> StopTighteningResult:
    """Attempts to solve for a tightened stop in the approved structural corridor:
    corridor = [current_stop, base_low - tick_size].
    """
    if position_shares <= 0:
        return StopTighteningResult(False, current_stop, Decimal("0"), base_low - tick_size)

    corridor_max = floor_to_tick(base_low - tick_size, tick_size)
    if corridor_max <= current_stop:
        return StopTighteningResult(False, current_stop, Decimal("0"), corridor_max)

    # Required per-share risk = approved_max_risk / position_shares
    # entry_vwap - required_stop <= allowed_risk_per_share => required_stop >= entry_vwap - (approved_max_risk / shares)
    max_risk_per_share = approved_max_risk / Decimal(str(position_shares))
    raw_required_stop = entry_vwap - max_risk_per_share

    # Ceil to tick to guarantee risk <= approved_max_risk
    ticks = (raw_required_stop / tick_size).to_integral_value(rounding=ROUND_CEILING)
    tightened_stop = ticks * tick_size

    if tightened_stop > current_stop and tightened_stop <= corridor_max:
        actual_risk = Decimal(str(position_shares)) * (entry_vwap - tightened_stop)
        return StopTighteningResult(
            can_tighten=True,
            new_stop=tightened_stop,
            residual_risk=actual_risk,
            corridor_max=corridor_max,
        )

    return StopTighteningResult(
        can_tighten=False,
        new_stop=current_stop,
        residual_risk=Decimal(str(position_shares)) * (entry_vwap - current_stop),
        corridor_max=corridor_max,
    )


def solve_risk_reduction_exit(
    position_shares: int,
    entry_vwap: Decimal,
    effective_stop: Decimal,
    approved_max_risk: Decimal,
    max_notional_cap: Decimal,
    lot_size: int = 1,
    current_price: Decimal | None = None,
) -> RiskReductionResult:
    """Calculates minimum whole-share trim quantity needed to satisfy risk and notional caps.
    Rounds exit quantity UP (flooring remaining quantity) toward less risk.
    Tolerates residual overage <= 1 tradable lot.
    """
    if lot_size <= 0:
        raise ValueError("lot_size must be positive")

    per_share_risk = entry_vwap - effective_stop
    notional_price = current_price or entry_vwap
    if notional_price <= 0:
        raise ValueError("current_price must be positive")
    if per_share_risk <= 0:
        # Stop is at or above entry, open risk is zero
        return RiskReductionResult(
            exit_shares=0,
            remaining_shares=position_shares,
            remaining_risk=Decimal("0"),
            rounding_residual=Decimal("0"),
            is_successful=True,
        )

    # Max remaining shares allowed by risk budget (floor)
    max_risk_shares = int((approved_max_risk / per_share_risk).to_integral_value(rounding=ROUND_FLOOR))
    # Max remaining shares allowed by notional cap (floor)
    max_notional_shares = int((max_notional_cap / notional_price).to_integral_value(rounding=ROUND_FLOOR))

    raw_allowed_remaining = max(0, min(max_risk_shares, max_notional_shares))
    allowed_remaining = (raw_allowed_remaining // lot_size) * lot_size
    
    if allowed_remaining >= position_shares:
        # Position is within budget, no trim needed
        actual_risk = Decimal(str(position_shares)) * per_share_risk
        return RiskReductionResult(
            exit_shares=0,
            remaining_shares=position_shares,
            remaining_risk=actual_risk,
            rounding_residual=Decimal("0"),
            is_successful=True,
        )

    # Trim needed: round trim UP
    exit_shares = position_shares - allowed_remaining
    remaining_shares = allowed_remaining
    remaining_risk = Decimal(str(remaining_shares)) * per_share_risk

    # Check 1-lot residual tolerance quantum
    one_lot_risk_quantum = Decimal(str(lot_size)) * per_share_risk
    residual_overage = max(Decimal("0"), remaining_risk - approved_max_risk)
    is_successful = residual_overage <= one_lot_risk_quantum

    return RiskReductionResult(
        exit_shares=exit_shares,
        remaining_shares=remaining_shares,
        remaining_risk=remaining_risk,
        rounding_residual=residual_overage,
        is_successful=is_successful,
    )
