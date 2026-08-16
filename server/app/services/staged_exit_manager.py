"""Staged Exit Manager for P10 Positions.

Evaluates intraday ticks for staged target exits (T1, T2, T3, Runner),
cumulative gap exits, stop-loss ratchets, and 2x ATR14 high-water mark trailing stops
according to AGENTS.md §6.7.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.domain.p10_geometry import floor_to_tick


@dataclass
class StagedPositionState:
    id: UUID
    symbol: str
    side: str
    state: str
    open_quantity: int
    weighted_entry_price: Decimal
    current_stop: Decimal
    t1_target: Decimal | None
    t2_target: Decimal | None
    t3_target: Decimal | None
    t1_shares: int
    t2_shares: int
    t3_shares: int
    runner_shares: int
    t1_filled_shares: int
    t2_filled_shares: int
    t3_filled_shares: int
    runner_filled_shares: int
    high_water_mark: Decimal | None
    trailing_stop: Decimal | None
    atr14: Decimal
    tick_size: Decimal


@dataclass(frozen=True)
class StagedExitAction:
    action_type: str  # 'stop_loss', 'target_exit', 'trailing_exit', 'none'
    exit_shares: int
    trigger_price: Decimal
    exit_purpose: str
    new_stop: Decimal | None = None
    new_high_water_mark: Decimal | None = None
    new_trailing_stop: Decimal | None = None
    crossed_targets: tuple[int, ...] = ()


@dataclass(frozen=True)
class TargetFillAllocation:
    t1: int = 0
    t2: int = 0
    t3: int = 0


def allocate_cumulative_target_fill(
    pos: StagedPositionState,
    *,
    exit_purpose: str,
    fill_quantity: int,
) -> TargetFillAllocation:
    """Allocate one cumulative target fill from the lowest target upward."""
    highest = {"target_1": 1, "target_2": 2, "target_3": 3}.get(exit_purpose)
    if highest is None or fill_quantity <= 0:
        return TargetFillAllocation()
    remaining = fill_quantity
    allocated = [0, 0, 0]
    planned = (pos.t1_shares, pos.t2_shares, pos.t3_shares)
    filled = (pos.t1_filled_shares, pos.t2_filled_shares, pos.t3_filled_shares)
    for index in range(highest):
        amount = min(max(0, planned[index] - filled[index]), remaining)
        allocated[index] = amount
        remaining -= amount
        if remaining == 0:
            break
    return TargetFillAllocation(*allocated)


def evaluate_staged_position_tick(
    pos: StagedPositionState,
    ltp: Decimal,
) -> StagedExitAction:
    """Evaluates an LTP tick for a staged position:
    1. Stop Loss / Trailing Stop check: exits all remaining quantity
    2. Target exits: T1 (25%), T2 (25%), T3 (25%) with cumulative gap consolidation
    3. Runner trail: 2x ATR14 high-water mark trail
    """
    if pos.open_quantity <= 0:
        return StagedExitAction("none", 0, ltp, "none")

    is_trailing = (pos.state == "trailing_active" and pos.trailing_stop is not None)
    effective_stop = pos.trailing_stop if is_trailing else pos.current_stop

    # 1. Stop Loss or Trailing Stop Trigger (100% remaining)
    if ltp <= effective_stop:
        action_type = "trailing_exit" if is_trailing else "stop_loss"
        exit_purpose = "runner_trail" if is_trailing else "stop_loss"
        return StagedExitAction(
            action_type=action_type,
            exit_shares=pos.open_quantity,
            trigger_price=ltp,
            exit_purpose=exit_purpose,
        )

    # 2. Cumulative Target Checks (T1, T2, T3)
    target_shares_to_exit = 0
    purposes: list[str] = []
    crossed_targets: list[int] = []

    # Check T1
    if pos.t1_target and ltp >= pos.t1_target:
        unfilled_t1 = max(0, pos.t1_shares - pos.t1_filled_shares)
        if unfilled_t1 > 0:
            target_shares_to_exit += unfilled_t1
            purposes.append("target_1")
            crossed_targets.append(1)

    # Check T2
    if pos.t2_target and ltp >= pos.t2_target:
        unfilled_t2 = max(0, pos.t2_shares - pos.t2_filled_shares)
        if unfilled_t2 > 0:
            target_shares_to_exit += unfilled_t2
            purposes.append("target_2")
            crossed_targets.append(2)

    # Check T3
    if pos.t3_target and ltp >= pos.t3_target:
        unfilled_t3 = max(0, pos.t3_shares - pos.t3_filled_shares)
        if unfilled_t3 > 0:
            target_shares_to_exit += unfilled_t3
            purposes.append("target_3")
            crossed_targets.append(3)

    if target_shares_to_exit > 0:
        actual_exit_shares = min(target_shares_to_exit, pos.open_quantity)
        # The durable intent uses the highest crossed target as its enum. The
        # complete crossed set is carried separately for one cumulative order.
        purpose_str = purposes[-1]
        return StagedExitAction(
            action_type="target_exit",
            exit_shares=actual_exit_shares,
            trigger_price=ltp,
            exit_purpose=purpose_str,
            # Stop/trail transitions happen only after the corresponding fill.
            new_stop=None,
            new_high_water_mark=None,
            crossed_targets=tuple(crossed_targets),
        )

    # 3. Runner High-Water Mark Trailing Stop Ratchet (2x ATR14)
    if pos.state == "trailing_active" or (pos.t2_filled_shares >= pos.t2_shares and pos.t2_shares > 0):
        hwm = max(pos.high_water_mark or ltp, ltp)
        trail_distance = pos.atr14 * Decimal("2.00")
        candidate_trail = floor_to_tick(hwm - trail_distance, pos.tick_size)
        updated_trail_stop = max(pos.current_stop, pos.trailing_stop or Decimal("0"), candidate_trail)
        
        if updated_trail_stop > (pos.trailing_stop or Decimal("0")):
            return StagedExitAction(
                action_type="none",
                exit_shares=0,
                trigger_price=ltp,
                exit_purpose="trail_ratchet",
                new_stop=pos.current_stop,
                new_high_water_mark=hwm,
                new_trailing_stop=updated_trail_stop,
            )

    return StagedExitAction("none", 0, ltp, "none")
