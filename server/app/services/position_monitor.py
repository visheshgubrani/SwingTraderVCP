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
    complete_paper_exit,
    publish_tick_subscriptions,
)

logger = logging.getLogger("position_monitor")

EXIT_INTENT_SIDE = {
    "long": "sell",
    "short": "buy",
}


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
                i.tick_size
            FROM positions p
            JOIN instruments i ON i.id = p.instrument_id
            WHERE p.state NOT IN ('closed', 'cancelled')
              AND i.fyers_symbol IS NOT NULL
            """
        )
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
        logger.warning(
            "Position %s has unknown trailing rule '%s'; skipping trail update.",
            position.id,
            rule_type,
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

    if settings.execution_mode == "paper":
        await complete_paper_exit(
            db,
            order_intent_id=intent_ref.id,
            position_id=position.id,
            exit_price=signal.trigger_price,
        )
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


async def sync_tick_subscriptions(
    redis,
    positions: list[MonitoredPosition],
) -> None:
    symbols = sorted({position.symbol for position in positions})
    await publish_tick_subscriptions(redis, symbols)
