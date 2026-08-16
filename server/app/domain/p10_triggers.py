"""P10 Intraday Triggers & Multi-Leg Add State Machine.

Deterministic Python evaluation of 5-minute two-bar price + relative-volume confirmation,
and Hold(N) + Base(M) multi-leg add progression according to AGENTS.md §6.1 & §6.2.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence
from zoneinfo import ZoneInfo

from app.domain.p10_geometry import DEFAULT_TICK_SIZE, floor_to_tick
from app.domain.p10_sizing import EntryTemplate


SESSION_START_TIME = dt.time(9, 15)
SESSION_SKIP_UNTIL = dt.time(9, 30)  # Ignore first 15 minutes
SESSION_END_TIME = dt.time(15, 30)
IST_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class FiveMinuteBar:
    bar_time: dt.datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    cumulative_volume: int


@dataclass(frozen=True)
class DailySessionBar:
    date: dt.date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    ema21: Decimal
    ema21_5d_ago: Decimal
    sma_volume_20: int


@dataclass(frozen=True)
class TriggerEvaluationResult:
    is_triggered: bool
    signal_bar_valid: bool
    confirmation_bar_valid: bool
    chase_valid: bool
    signal_rvol: Decimal
    confirmation_rvol: Decimal
    rejection_reason: str | None = None


@dataclass(frozen=True)
class AddLegGateState:
    hold_required: int
    base_required: int
    current_hold_count: int
    current_base_count: int
    is_hold_satisfied: bool
    is_base_satisfied: bool
    is_trend_satisfied: bool
    is_gate_open: bool
    base_low: Decimal | None
    base_high: Decimal | None
    recommended_new_stop: Decimal | None
    rejection_reason: str | None = None


def calculate_relative_volume(
    cumulative_volume: int,
    adv20_robust: int,
    expected_fraction: Decimal,
) -> Decimal:
    """Calculates relative volume = cumulative_volume / (adv20_robust * expected_fraction)."""
    if adv20_robust <= 0 or expected_fraction <= 0:
        return Decimal("0")

    expected_cumulative = Decimal(str(adv20_robust)) * expected_fraction
    if expected_cumulative <= 0:
        return Decimal("0")

    rvol = Decimal(str(cumulative_volume)) / expected_cumulative
    return rvol.quantize(Decimal("0.0001"))


def evaluate_intraday_trigger(
    signal_bar: FiveMinuteBar,
    confirmation_bar: FiveMinuteBar,
    trigger_price: Decimal,
    chase_ceiling: Decimal,
    adv20_robust: int,
    signal_expected_fraction: Decimal,
    conf_expected_fraction: Decimal,
    required_rvol: Decimal,
    current_market_price: Decimal,
) -> TriggerEvaluationResult:
    """Evaluates two-bar price + relative volume trigger confirmation:
    1. First 15 minutes of the session (09:15-09:30) are excluded.
    2. Signal bar must close above trigger with relative volume >= required_rvol.
    3. Confirmation bar (immediately following) must remain above trigger with rvol >= required_rvol.
    4. Current price must be at or below chase ceiling.
    """
    def market_time(value: dt.datetime) -> dt.datetime:
        # Stored bars are timezone-aware.  Treat naive values as IST only for
        # deterministic domain tests and legacy rows.
        if value.tzinfo is None:
            return value.replace(tzinfo=IST_TZ)
        return value.astimezone(IST_TZ)

    signal_at = market_time(signal_bar.bar_time)
    confirmation_at = market_time(confirmation_bar.bar_time)

    # A confirmation is valid only for the immediately following completed
    # five-minute bucket in the same NSE session.
    if (
        confirmation_at.date() != signal_at.date()
        or confirmation_at - signal_at != dt.timedelta(minutes=5)
    ):
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=False,
            confirmation_bar_valid=False,
            chase_valid=False,
            signal_rvol=Decimal("0"),
            confirmation_rvol=Decimal("0"),
            rejection_reason="Confirmation bar is not the immediately following 5-minute bar",
        )

    # 1. First 15 minute and market-session filters.
    if (
        signal_at.time() < SESSION_SKIP_UNTIL
        or signal_at.time() >= SESSION_END_TIME
        or confirmation_at.time() >= SESSION_END_TIME
    ):
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=False,
            confirmation_bar_valid=False,
            chase_valid=False,
            signal_rvol=Decimal("0"),
            confirmation_rvol=Decimal("0"),
            rejection_reason=(
                f"Signal/confirmation bars at {signal_at.time()} and "
                f"{confirmation_at.time()} fall outside the eligible NSE window "
                "(including the first 15-minute exclusion window)"
            ),
        )

    # 2. Relative volume calculations
    sig_rvol = calculate_relative_volume(signal_bar.cumulative_volume, adv20_robust, signal_expected_fraction)
    conf_rvol = calculate_relative_volume(confirmation_bar.cumulative_volume, adv20_robust, conf_expected_fraction)

    # 3. Signal bar check
    sig_valid = (signal_bar.close > trigger_price) and (sig_rvol >= required_rvol)
    if not sig_valid:
        reason = []
        if signal_bar.close <= trigger_price:
            reason.append(f"Signal bar close ({signal_bar.close}) <= trigger ({trigger_price})")
        if sig_rvol < required_rvol:
            reason.append(f"Signal bar rvol ({sig_rvol:.2f}) < required ({required_rvol:.2f})")
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=False,
            confirmation_bar_valid=False,
            chase_valid=False,
            signal_rvol=sig_rvol,
            confirmation_rvol=conf_rvol,
            rejection_reason="; ".join(reason),
        )

    # 4. Confirmation bar check (must be subsequent bar and hold above trigger + rvol)
    conf_valid = (confirmation_bar.close > trigger_price) and (conf_rvol >= required_rvol)
    if not conf_valid:
        reason = []
        if confirmation_bar.close <= trigger_price:
            reason.append(f"Confirmation bar close ({confirmation_bar.close}) <= trigger ({trigger_price})")
        if conf_rvol < required_rvol:
            reason.append(f"Confirmation bar rvol ({conf_rvol:.2f}) < required ({required_rvol:.2f})")
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=True,
            confirmation_bar_valid=False,
            chase_valid=False,
            signal_rvol=sig_rvol,
            confirmation_rvol=conf_rvol,
            rejection_reason="; ".join(reason),
        )

    # 5. Chase ceiling check
    chase_ok = current_market_price <= chase_ceiling
    if not chase_ok:
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=True,
            confirmation_bar_valid=True,
            chase_valid=False,
            signal_rvol=sig_rvol,
            confirmation_rvol=conf_rvol,
            rejection_reason=f"Current market price ({current_market_price}) exceeds chase ceiling ({chase_ceiling})",
        )

    return TriggerEvaluationResult(
        is_triggered=True,
        signal_bar_valid=True,
        confirmation_bar_valid=True,
        chase_valid=True,
        signal_rvol=sig_rvol,
        confirmation_rvol=conf_rvol,
        rejection_reason=None,
    )


def get_add_leg_gate_requirements(template: EntryTemplate | str, leg_index: int) -> tuple[int, int]:
    """Returns (hold_required, base_required) for a given template and leg index."""
    tmpl = EntryTemplate(template) if isinstance(template, str) else template
    if leg_index == 2:
        if tmpl == EntryTemplate.THREE_LEG_FRONT:
            return 1, 2  # Hold(1), Base(2)
        else:
            return 2, 3  # Hold(2), Base(3)
    elif leg_index == 3:
        return 2, 3      # Hold(2), Base(3)
    else:
        return 0, 0


def evaluate_add_leg_gates(
    template: EntryTemplate | str,
    leg_index: int,
    preceding_leg_trigger: Decimal,
    preceding_leg_high: Decimal,
    atr14: Decimal,
    completed_sessions_since_fill: Sequence[DailySessionBar],
    current_stop: Decimal,
    tick_size: Decimal = DEFAULT_TICK_SIZE,
) -> AddLegGateState:
    """Evaluates sequential Hold(N) and Base(M) add-leg gates across completed daily sessions."""
    hold_req, base_req = get_add_leg_gate_requirements(template, leg_index)
    if hold_req == 0 and base_req == 0:
        return AddLegGateState(
            hold_required=0,
            base_required=0,
            current_hold_count=0,
            current_base_count=0,
            is_hold_satisfied=True,
            is_base_satisfied=True,
            is_trend_satisfied=True,
            is_gate_open=True,
            base_low=None,
            base_high=None,
            recommended_new_stop=None,
            rejection_reason=None,
        )

    if not completed_sessions_since_fill:
        return AddLegGateState(
            hold_required=hold_req,
            base_required=base_req,
            current_hold_count=0,
            current_base_count=0,
            is_hold_satisfied=False,
            is_base_satisfied=False,
            is_trend_satisfied=False,
            is_gate_open=False,
            base_low=None,
            base_high=None,
            recommended_new_stop=None,
            rejection_reason="No completed daily sessions recorded since preceding fill",
        )

    # 1. Evaluate Hold(N): consecutive daily closes >= preceding_leg_trigger
    # A close below resets the count to 0
    hold_count = 0
    hold_satisfied = False
    hold_satisfaction_idx = -1

    for idx, s in enumerate(completed_sessions_since_fill):
        if s.close >= preceding_leg_trigger:
            hold_count += 1
            if hold_count >= hold_req:
                hold_satisfied = True
                hold_satisfaction_idx = idx
                break
        else:
            hold_count = 0

    if not hold_satisfied:
        return AddLegGateState(
            hold_required=hold_req,
            base_required=base_req,
            current_hold_count=hold_count,
            current_base_count=0,
            is_hold_satisfied=False,
            is_base_satisfied=False,
            is_trend_satisfied=False,
            is_gate_open=False,
            base_low=None,
            base_high=None,
            recommended_new_stop=None,
            rejection_reason=f"Hold({hold_req}) not yet satisfied (current hold count: {hold_count})",
        )

    # 2. Evaluate Base(M) on sessions strictly following Hold satisfaction
    base_sessions = completed_sessions_since_fill[hold_satisfaction_idx + 1:]
    base_count = 0
    base_lows: list[Decimal] = []
    base_highs: list[Decimal] = []

    max_range = atr14 * Decimal("1.50")
    max_high_overhead = preceding_leg_high + (atr14 * Decimal("0.25"))

    for s in base_sessions:
        day_range = s.high - s.low
        is_range_tight = day_range <= max_range
        is_high_contained = s.high <= max_high_overhead
        is_volume_dry = s.volume < s.sma_volume_20

        if is_range_tight and is_high_contained and is_volume_dry:
            base_count += 1
            base_lows.append(s.low)
            base_highs.append(s.high)
            if base_count >= base_req:
                break
        else:
            # Base must be consecutive M sessions
            base_count = 0
            base_lows.clear()
            base_highs.clear()

    base_satisfied = base_count >= base_req
    if not base_satisfied:
        return AddLegGateState(
            hold_required=hold_req,
            base_required=base_req,
            current_hold_count=hold_count,
            current_base_count=base_count,
            is_hold_satisfied=True,
            is_base_satisfied=False,
            is_trend_satisfied=False,
            is_gate_open=False,
            base_low=None,
            base_high=None,
            recommended_new_stop=None,
            rejection_reason=f"Base({base_req}) not yet satisfied (current base count: {base_count})",
        )

    # 3. Evaluate Trend Filter on latest session: close > EMA21 and EMA21_today > EMA21_5d_ago
    latest = completed_sessions_since_fill[-1]
    trend_satisfied = (latest.close > latest.ema21) and (latest.ema21 > latest.ema21_5d_ago)

    if not trend_satisfied:
        return AddLegGateState(
            hold_required=hold_req,
            base_required=base_req,
            current_hold_count=hold_count,
            current_base_count=base_count,
            is_hold_satisfied=True,
            is_base_satisfied=True,
            is_trend_satisfied=False,
            is_gate_open=False,
            base_low=min(base_lows) if base_lows else None,
            base_high=max(base_highs) if base_highs else None,
            recommended_new_stop=None,
            rejection_reason=f"Trend filter failed: close ({latest.close}) > EMA21 ({latest.ema21}) and EMA21 rising ({latest.ema21} > {latest.ema21_5d_ago})",
        )

    # All gates open!
    actual_base_low = min(base_lows)
    actual_base_high = max(base_highs)
    
    # Recommended new stop = max(current_stop, base_low - 0.25 * ATR14) snapped to tick
    raw_new_stop = actual_base_low - (atr14 * Decimal("0.25"))
    snapped_new_stop = floor_to_tick(raw_new_stop, tick_size)
    ratcheted_stop = max(current_stop, snapped_new_stop)

    return AddLegGateState(
        hold_required=hold_req,
        base_required=base_req,
        current_hold_count=hold_count,
        current_base_count=base_count,
        is_hold_satisfied=True,
        is_base_satisfied=True,
        is_trend_satisfied=True,
        is_gate_open=True,
        base_low=actual_base_low,
        base_high=actual_base_high,
        recommended_new_stop=ratcheted_stop,
        rejection_reason=None,
    )
