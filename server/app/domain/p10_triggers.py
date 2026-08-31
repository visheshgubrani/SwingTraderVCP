"""P10 Intraday Triggers & Multi-Leg Add State Machine.

Deterministic Python evaluation of versioned 5-minute breakout confirmation
and Hold(N) + Base(M) multi-leg add progression according to AGENTS.md §6.
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

# An entry window closes at 16:00 IST on its final eligible session (D1 for the
# initial leg, the 10-session expiry date for add legs). The cutoff sits after
# the last intraday bar-reconciliation cron tick (15:45:20), which verifies and
# re-publishes the final 15:25-15:30 five-minute bars, so a legitimate last-bar
# two-bar confirmation and its allocation can never be cut off by expiry.
ENTRY_WINDOW_CLOSE_TIME = dt.time(16, 0)
CUMULATIVE_TWO_BAR_POLICY_V1 = "cumulative_two_bar_v1"
BREAKOUT_BAR_SIGNAL_POLICY_V2 = "breakout_bar_signal_v2"
BALANCED_BREAKOUT_POLICY_V3 = "balanced_breakout_v3"


def entry_window_closed(
    eligible_session_end: dt.date | None,
    now_ist: dt.datetime,
) -> bool:
    """True once the eligible entry window has closed for good.

    A leg with no recorded eligible session end (legacy rows) is never
    auto-expired: fail conservatively toward leaving it visible for review.
    """
    if eligible_session_end is None:
        return False
    if now_ist.tzinfo is None:
        now_ist = now_ist.replace(tzinfo=IST_TZ)
    else:
        now_ist = now_ist.astimezone(IST_TZ)
    deadline = dt.datetime.combine(
        eligible_session_end, ENTRY_WINDOW_CLOSE_TIME, tzinfo=IST_TZ
    )
    return now_ist >= deadline


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
    signal_session_rvol: Decimal = Decimal("0")
    confirmation_session_rvol: Decimal = Decimal("0")
    rejection_reason: str | None = None


@dataclass(frozen=True)
class BalancedBreakoutEvaluation:
    is_triggered: bool
    signal_bar_valid: bool
    signal_rvol: Decimal
    signal_session_rvol: Decimal = Decimal("0")
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


def calculate_bar_relative_volume(
    bar_volume: int,
    adv20_robust: int,
    expected_bar_fraction: Decimal,
) -> Decimal:
    """Return one 5-minute bar's volume relative to its time-of-day profile."""
    if bar_volume < 0 or adv20_robust <= 0 or expected_bar_fraction <= 0:
        return Decimal("0")
    expected_bar_volume = Decimal(str(adv20_robust)) * expected_bar_fraction
    if expected_bar_volume <= 0:
        return Decimal("0")
    return (
        Decimal(str(bar_volume)) / expected_bar_volume
    ).quantize(Decimal("0.0001"))


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
    policy_version: str = CUMULATIVE_TWO_BAR_POLICY_V1,
    signal_expected_bar_fraction: Decimal | None = None,
    conf_expected_bar_fraction: Decimal | None = None,
) -> TriggerEvaluationResult:
    """Evaluate a versioned two-bar breakout.

    ``cumulative_two_bar_v1`` preserves the historical two-cumulative-RVOL
    gates and confirmation-time chase check. ``breakout_bar_signal_v2`` gates
    only the signal bar's individual-bucket RVOL; confirmation is price-only
    and chase remains a separate execution-eligibility diagnostic.

    1. First 15 minutes of the session (09:15-09:30) are excluded.
    2. Signal bar must close above trigger with the policy's required RVOL.
    3. Confirmation must be the immediately following bar and hold the trigger.
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

    # 2. Relative-volume calculations. Session-cumulative values remain
    # diagnostics in v2 and the authoritative values in v1.
    sig_session_rvol = calculate_relative_volume(
        signal_bar.cumulative_volume, adv20_robust, signal_expected_fraction
    )
    conf_session_rvol = calculate_relative_volume(
        confirmation_bar.cumulative_volume, adv20_robust, conf_expected_fraction
    )
    if policy_version == BREAKOUT_BAR_SIGNAL_POLICY_V2:
        if signal_expected_bar_fraction is None or conf_expected_bar_fraction is None:
            raise ValueError("v2 trigger evaluation requires both expected bar fractions")
        sig_rvol = calculate_bar_relative_volume(
            signal_bar.volume, adv20_robust, signal_expected_bar_fraction
        )
        conf_rvol = calculate_bar_relative_volume(
            confirmation_bar.volume, adv20_robust, conf_expected_bar_fraction
        )
    elif policy_version == CUMULATIVE_TWO_BAR_POLICY_V1:
        sig_rvol = sig_session_rvol
        conf_rvol = conf_session_rvol
    else:
        raise ValueError(f"Unknown entry trigger policy: {policy_version}")

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
            signal_session_rvol=sig_session_rvol,
            confirmation_session_rvol=conf_session_rvol,
            rejection_reason="; ".join(reason),
        )

    # 4. Confirmation is price-only in v2; v1 retains its cumulative-RVOL gate.
    conf_volume_valid = (
        conf_rvol >= required_rvol
        if policy_version == CUMULATIVE_TWO_BAR_POLICY_V1
        else True
    )
    conf_valid = confirmation_bar.close > trigger_price and conf_volume_valid
    if not conf_valid:
        reason = []
        if confirmation_bar.close <= trigger_price:
            reason.append(f"Confirmation bar close ({confirmation_bar.close}) <= trigger ({trigger_price})")
        if not conf_volume_valid:
            reason.append(f"Confirmation bar rvol ({conf_rvol:.2f}) < required ({required_rvol:.2f})")
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=True,
            confirmation_bar_valid=False,
            chase_valid=False,
            signal_rvol=sig_rvol,
            confirmation_rvol=conf_rvol,
            signal_session_rvol=sig_session_rvol,
            confirmation_session_rvol=conf_session_rvol,
            rejection_reason="; ".join(reason),
        )

    # 5. Chase is part of v1 trigger validity, but only an execution
    # eligibility diagnostic in v2.
    chase_ok = current_market_price <= chase_ceiling
    if not chase_ok and policy_version == CUMULATIVE_TWO_BAR_POLICY_V1:
        return TriggerEvaluationResult(
            is_triggered=False,
            signal_bar_valid=True,
            confirmation_bar_valid=True,
            chase_valid=False,
            signal_rvol=sig_rvol,
            confirmation_rvol=conf_rvol,
            signal_session_rvol=sig_session_rvol,
            confirmation_session_rvol=conf_session_rvol,
            rejection_reason=f"Current market price ({current_market_price}) exceeds chase ceiling ({chase_ceiling})",
        )

    return TriggerEvaluationResult(
        is_triggered=True,
        signal_bar_valid=True,
        confirmation_bar_valid=True,
        chase_valid=chase_ok,
        signal_rvol=sig_rvol,
        confirmation_rvol=conf_rvol,
        signal_session_rvol=sig_session_rvol,
        confirmation_session_rvol=conf_session_rvol,
        rejection_reason=None,
    )


def evaluate_balanced_breakout_signal(
    *,
    signal_bar: FiveMinuteBar,
    trigger_price: Decimal,
    adv20_robust: int,
    expected_bar_fraction: Decimal,
    expected_cumulative_fraction: Decimal,
    required_rvol: Decimal,
) -> BalancedBreakoutEvaluation:
    """Evaluate a single 5-minute breakout signal bar under balanced_breakout_v3.

    1. First 15 minutes of the session (09:15-09:30) are excluded (09:30 is inclusive; 15:30 is exclusive).
    2. Signal bar must close strictly above trigger price.
    3. Signal bar's time-of-day bucket RVOL must meet or exceed the template's required threshold.
    4. Session cumulative RVOL is computed and returned as a diagnostic.
    """
    def market_time(value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=IST_TZ)
        return value.astimezone(IST_TZ)

    signal_at = market_time(signal_bar.bar_time)

    # 1. Market session & first 15-minute exclusion window (09:30 inclusive, 15:30 exclusive)
    if signal_at.time() < SESSION_SKIP_UNTIL or signal_at.time() >= SESSION_END_TIME:
        return BalancedBreakoutEvaluation(
            is_triggered=False,
            signal_bar_valid=False,
            signal_rvol=Decimal("0"),
            signal_session_rvol=Decimal("0"),
            rejection_reason=(
                f"Signal bar at {signal_at.time()} falls outside the eligible NSE window "
                "(including the first 15-minute exclusion window)"
            ),
        )

    # 2. RVOL calculations
    sig_bar_rvol = calculate_bar_relative_volume(
        signal_bar.volume, adv20_robust, expected_bar_fraction
    )
    sig_session_rvol = calculate_relative_volume(
        signal_bar.cumulative_volume, adv20_robust, expected_cumulative_fraction
    )

    # 3. Price & volume gates
    price_passed = signal_bar.close > trigger_price
    volume_passed = sig_bar_rvol >= required_rvol

    if not price_passed or not volume_passed:
        reasons = []
        if not price_passed:
            reasons.append(f"Signal bar close ({signal_bar.close}) <= trigger ({trigger_price})")
        if not volume_passed:
            reasons.append(f"Signal bar rvol ({sig_bar_rvol:.2f}) < required ({required_rvol:.2f})")
        return BalancedBreakoutEvaluation(
            is_triggered=False,
            signal_bar_valid=False,
            signal_rvol=sig_bar_rvol,
            signal_session_rvol=sig_session_rvol,
            rejection_reason="; ".join(reasons),
        )

    return BalancedBreakoutEvaluation(
        is_triggered=True,
        signal_bar_valid=True,
        signal_rvol=sig_bar_rvol,
        signal_session_rvol=sig_session_rvol,
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
