"""Position monitor business logic — SL/target/trail evaluation on LTP ticks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.trading import (
    ExitSignal,
    apply_step_pct_trail,
    evaluate_exit,
)
from app.services.execution_engine import (
    create_exit_intent,
    publish_tick_subscriptions,
)
from app.services.staged_exit_manager import (
    StagedPositionState,
    evaluate_staged_position_tick,
)

logger = logging.getLogger("position_monitor")

EXIT_INTENT_SIDE = {
    "long": "sell",
    "short": "buy",
}


async def _emit_monitor_critical(
    db: AsyncSession,
    *,
    position_id: UUID,
    event_type: str,
    details: dict[str, Any],
) -> None:
    await db.execute(
        text(
            """
            INSERT INTO system_events (
                component, severity, event_type, correlation_id,
                position_id, payload
            ) SELECT
                'position_monitor', 'critical', :event_type, :correlation_id,
                :position_id, CAST(:payload AS jsonb)
            WHERE NOT EXISTS (
                SELECT 1 FROM system_events
                WHERE component = 'position_monitor'
                  AND event_type = :event_type
                  AND position_id = :position_id
                  AND event_ts >= now() - interval '1 hour'
            )
            """
        ),
        {
            "event_type": event_type,
            "correlation_id": position_id,
            "position_id": position_id,
            "payload": json.dumps(details),
        },
    )


@dataclass
class MonitoredPosition:
    id: UUID
    symbol: str
    side: str
    state: str
    quantity: int
    open_quantity: int
    product_type: str
    average_entry_price: Decimal | None
    current_stop_loss: Decimal
    current_target: Decimal | None
    trailing_rule: dict[str, Any]
    tick_size: Decimal
    proposal_id: UUID | None = None
    trailing_rule_type: str | None = None
    high_water_mark: Decimal | None = None
    trailing_stop: Decimal | None = None
    t1_target: Decimal | None = None
    t2_target: Decimal | None = None
    t3_target: Decimal | None = None
    t1_shares: int = 0
    t2_shares: int = 0
    t3_shares: int = 0
    runner_shares: int = 0
    t1_filled_shares: int = 0
    t2_filled_shares: int = 0
    t3_filled_shares: int = 0
    runner_filled_shares: int = 0
    atr14: Decimal | None = None


async def load_monitored_positions(db: AsyncSession) -> list[MonitoredPosition]:
    result = await db.execute(
        text(
            """
            SELECT
                p.id,
                i.fyers_symbol AS symbol,
                p.side,
                p.state,
                p.quantity,
                p.open_quantity,
                p.product_type,
                p.average_entry_price,
                p.current_stop_loss,
                p.current_target,
                p.trailing_rule,
                i.tick_size,
                p.proposal_id,
                p.trailing_rule_type,
                p.high_water_mark,
                p.trailing_stop,
                p.t1_target,
                p.t2_target,
                p.t3_target,
                p.t1_shares,
                p.t2_shares,
                p.t3_shares,
                p.runner_shares,
                p.t1_filled_shares,
                p.t2_filled_shares,
                p.t3_filled_shares,
                p.runner_filled_shares,
                COALESCE(
                    NULLIF(p.trailing_rule->>'atr14', '')::numeric,
                    NULLIF(tp.geometry->>'atr14', '')::numeric
                ) AS atr14
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            LEFT JOIN trade_proposals tp ON tp.id = p.proposal_id
            WHERE p.state NOT IN ('closed', 'cancelled')
              AND p.execution_mode = :execution_mode
              AND i.fyers_symbol IS NOT NULL
            """
        ),
        {"execution_mode": settings.execution_mode},
    )
    positions: list[MonitoredPosition] = []
    for row in result.mappings().all():
        if row["current_stop_loss"] is None:
            continue
        positions.append(
            MonitoredPosition(
                id=row["id"],
                symbol=row["symbol"],
                side=row["side"],
                state=row["state"],
                quantity=int(row["quantity"]),
                open_quantity=int(row["open_quantity"]),
                product_type=row["product_type"],
                average_entry_price=(
                    Decimal(row["average_entry_price"])
                    if row["average_entry_price"] is not None
                    else None
                ),
                current_stop_loss=Decimal(row["current_stop_loss"]),
                current_target=(
                    Decimal(row["current_target"])
                    if row["current_target"] is not None
                    else None
                ),
                trailing_rule=dict(row["trailing_rule"] or {}),
                tick_size=Decimal(row["tick_size"]),
                proposal_id=row["proposal_id"],
                trailing_rule_type=row["trailing_rule_type"],
                high_water_mark=(
                    Decimal(row["high_water_mark"])
                    if row["high_water_mark"] is not None
                    else None
                ),
                trailing_stop=(
                    Decimal(row["trailing_stop"])
                    if row["trailing_stop"] is not None
                    else None
                ),
                t1_target=(Decimal(row["t1_target"]) if row["t1_target"] is not None else None),
                t2_target=(Decimal(row["t2_target"]) if row["t2_target"] is not None else None),
                t3_target=(Decimal(row["t3_target"]) if row["t3_target"] is not None else None),
                t1_shares=int(row["t1_shares"]),
                t2_shares=int(row["t2_shares"]),
                t3_shares=int(row["t3_shares"]),
                runner_shares=int(row["runner_shares"]),
                t1_filled_shares=int(row["t1_filled_shares"]),
                t2_filled_shares=int(row["t2_filled_shares"]),
                t3_filled_shares=int(row["t3_filled_shares"]),
                runner_filled_shares=int(row["runner_filled_shares"]),
                atr14=(Decimal(row["atr14"]) if row["atr14"] is not None else None),
            )
        )
    return positions


async def apply_trailing_update(
    db: AsyncSession,
    *,
    position: MonitoredPosition,
    ltp: Decimal,
) -> MonitoredPosition:
    """Ratchet the stop when a supported trailing rule is active."""
    rule_type = position.trailing_rule.get("type", "none")
    if rule_type == "none":
        return position
    if rule_type == "atr":
        logger.info(
            "Position %s uses ATR trailing; monitor skips until ATR feed exists.",
            position.id,
        )
        return position
    if rule_type != "step_pct":
        logger.critical(
            "Position %s has unknown trailing rule '%s'; skipping trail update.",
            position.id,
            rule_type,
        )
        await _emit_monitor_critical(
            db,
            position_id=position.id,
            event_type="unknown_trailing_rule",
            details={"rule_type": rule_type},
        )
        return position

    raw_value = position.trailing_rule.get("value")
    if raw_value is None:
        return position

    step_pct = Decimal(str(raw_value))
    new_stop = apply_step_pct_trail(
        side=position.side,
        ltp=ltp,
        current_stop=position.current_stop_loss,
        step_pct=step_pct,
        tick_size=position.tick_size,
    )
    if new_stop is None:
        return position

    from_state = position.state
    to_state = "trailing_active"
    await db.execute(
        text(
            """
            UPDATE positions
            SET
                current_stop_loss = :current_stop_loss,
                state = CASE
                    WHEN state = 'open' THEN 'trailing_active'
                    ELSE state
                END
            WHERE id = :position_id
              AND state IN ('open', 'trailing_active')
            """
        ),
        {
            "position_id": position.id,
            "current_stop_loss": new_stop,
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_type,
                from_state,
                to_state,
                trigger_source,
                observed_price,
                stop_loss_price,
                trailing_rule,
                details
            )
            VALUES (
                :position_id,
                'trailing_stop_updated',
                :from_state,
                :to_state,
                'position_monitor',
                :observed_price,
                :stop_loss_price,
                CAST(:trailing_rule AS jsonb),
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position.id,
            "from_state": from_state,
            "to_state": to_state,
            "observed_price": ltp,
            "stop_loss_price": new_stop,
            "trailing_rule": json.dumps(position.trailing_rule),
            "details": json.dumps(
                {
                    "previous_stop": str(position.current_stop_loss),
                    "new_stop": str(new_stop),
                    "ltp": str(ltp),
                }
            ),
        },
    )
    position.current_stop_loss = new_stop
    position.state = to_state
    return position


async def trigger_exit_for_signal(
    db: AsyncSession,
    *,
    position: MonitoredPosition,
    signal: ExitSignal,
) -> UUID | None:
    """Create and fill a paper exit intent. Returns intent id for live submission."""
    if position.open_quantity <= 0:
        return None

    exit_side = EXIT_INTENT_SIDE[position.side]
    reason = (
        f"P5 monitor exit: {signal.intent_type} at LTP {signal.trigger_price}."
    )
    intent_ref = await create_exit_intent(
        db,
        position_id=position.id,
        intent_type=signal.intent_type,
        side=exit_side,
        quantity=position.open_quantity,
        product_type=position.product_type,
        observed_price=signal.trigger_price,
        reason=reason,
    )
    if intent_ref is None:
        return None

    return intent_ref.id


async def process_position_tick(
    db: AsyncSession,
    *,
    position: MonitoredPosition,
    ltp: Decimal,
    kill_switch_engaged: bool,
) -> UUID | None:
    """
    Evaluate one LTP tick for a monitored position.

    Returns an exit intent id when live submission should follow commit.
    """
    if kill_switch_engaged:
        return None
    if position.state not in {"open", "trailing_active"}:
        return None
    if position.open_quantity <= 0:
        return None

    if position.trailing_rule_type == "p10_staged_atr":
        return await _process_p10_position_tick(db, position=position, ltp=ltp)

    position = await apply_trailing_update(db, position=position, ltp=ltp)
    signal = evaluate_exit(
        side=position.side,
        ltp=ltp,
        stop=position.current_stop_loss,
        target=position.current_target,
        trailing_active=position.state == "trailing_active",
    )
    if signal is None:
        return None

    return await trigger_exit_for_signal(
        db,
        position=position,
        signal=signal,
    )


async def _process_p10_position_tick(
    db: AsyncSession,
    *,
    position: MonitoredPosition,
    ltp: Decimal,
) -> UUID | None:
    """Evaluate the deterministic P10 25/25/25/runner policy."""
    if position.average_entry_price is None or position.atr14 is None or position.atr14 <= 0:
        logger.critical(
            "P10 position %s is missing weighted entry or ATR14; refusing to invent exit rules.",
            position.id,
        )
        await _emit_monitor_critical(
            db,
            position_id=position.id,
            event_type="p10_exit_inputs_missing",
            details={
                "average_entry_price": str(position.average_entry_price),
                "atr14": str(position.atr14),
            },
        )
        return None

    staged = StagedPositionState(
        id=position.id,
        symbol=position.symbol,
        side=position.side,
        state=position.state,
        open_quantity=position.open_quantity,
        weighted_entry_price=position.average_entry_price,
        current_stop=position.current_stop_loss,
        t1_target=position.t1_target,
        t2_target=position.t2_target,
        t3_target=position.t3_target,
        t1_shares=position.t1_shares,
        t2_shares=position.t2_shares,
        t3_shares=position.t3_shares,
        runner_shares=position.runner_shares,
        t1_filled_shares=position.t1_filled_shares,
        t2_filled_shares=position.t2_filled_shares,
        t3_filled_shares=position.t3_filled_shares,
        runner_filled_shares=position.runner_filled_shares,
        high_water_mark=position.high_water_mark,
        trailing_stop=position.trailing_stop,
        atr14=position.atr14,
        tick_size=position.tick_size,
    )
    action = evaluate_staged_position_tick(staged, ltp)
    if action.exit_purpose == "trail_ratchet":
        await db.execute(
            text(
                """
                UPDATE positions
                SET state = 'trailing_active',
                    high_water_mark = :high_water_mark,
                    trailing_stop = :trailing_stop
                WHERE id = :position_id
                  AND state IN ('open', 'trailing_active')
                  AND (:trailing_stop >= COALESCE(trailing_stop, 0))
                """
            ),
            {
                "position_id": position.id,
                "high_water_mark": action.new_high_water_mark,
                "trailing_stop": action.new_trailing_stop,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO position_events (
                    position_id, event_type, from_state, to_state,
                    trigger_source, observed_price, stop_loss_price, details
                ) VALUES (
                    :position_id, 'trailing_stop_updated', :from_state,
                    'trailing_active', 'position_monitor', :ltp, :stop,
                    CAST(:details AS jsonb)
                )
                """
            ),
            {
                "position_id": position.id,
                "from_state": position.state,
                "ltp": ltp,
                "stop": action.new_trailing_stop,
                "details": json.dumps(
                    {
                        "rule": "2x_atr14_high_water_mark",
                        "atr14": str(position.atr14),
                        "high_water_mark": str(action.new_high_water_mark),
                    }
                ),
            },
        )
        return None
    if action.action_type == "none" or action.exit_shares <= 0:
        return None

    if action.action_type == "target_exit":
        outstanding = await db.execute(
            text(
                """
                SELECT 1 FROM order_intents
                WHERE position_id = :position_id
                  AND intent_type = 'target_exit'
                  AND status IN (
                      'created', 'submission_pending', 'submitted',
                      'acknowledged', 'partially_filled', 'submission_unknown',
                      'cancel_requested'
                  )
                LIMIT 1
                """
            ),
            {"position_id": position.id},
        )
        if outstanding.scalar_one_or_none() is not None:
            return None
        # Profit-taking begins at the first T1 trigger, not its eventual fill.
        await db.execute(
            text(
                """
                UPDATE entry_legs
                SET status = 'cancelled', updated_at = now()
                WHERE position_id = :position_id
                  AND status IN (
                      'planned', 'armed', 'trigger_observed',
                      'waiting_for_reset'
                  )
                """
            ),
            {"position_id": position.id},
        )

    intent_type = {
        "stop_loss": "stop_loss_exit",
        "target_exit": "target_exit",
        "trailing_exit": "trailing_exit",
    }[action.action_type]
    suffix = action.exit_purpose
    if action.crossed_targets:
        suffix = f"targets-through-{max(action.crossed_targets)}"
    intent = await create_exit_intent(
        db,
        position_id=position.id,
        intent_type=intent_type,
        side=EXIT_INTENT_SIDE[position.side],
        quantity=action.exit_shares,
        product_type=position.product_type,
        observed_price=action.trigger_price,
        reason=(
            f"P10 deterministic {action.exit_purpose} at LTP "
            f"{action.trigger_price}."
        ),
        idempotency_suffix=f"p10:{suffix}",
        exit_purpose=action.exit_purpose,
        is_partial=action.exit_shares < position.open_quantity,
    )
    if intent is None:
        return None
    return intent.id


async def sync_tick_subscriptions(
    redis,
    positions: list[MonitoredPosition],
) -> None:
    symbols = sorted({position.symbol for position in positions})
    await publish_tick_subscriptions(redis, symbols)
