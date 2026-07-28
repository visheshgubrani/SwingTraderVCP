import json
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading import TradeValidationError, validate_trade_plan
from app.schemas.trading import (
    OrderIntentView,
    PositionView,
    TradeConfirmationResult,
    TradeInstructionCreate,
    TradeInstructionView,
)
from app.config import settings
from app.services.execution_engine import create_entry_intent, ensure_orders_allowed


class TradeNotFoundError(LookupError):
    pass


class TradeConflictError(RuntimeError):
    pass


TRADE_INSTRUCTION_SELECT = """
    SELECT
        ti.id,
        ti.instrument_id,
        ti.screening_result_id,
        i.fyers_symbol AS symbol,
        i.symbol AS display_symbol,
        ti.side,
        ti.quantity,
        ti.product_type,
        ti.entry_order_type,
        ti.planned_entry_price,
        ti.entry_limit_price,
        ti.initial_stop_loss,
        ti.initial_target,
        ti.trailing_rule,
        ti.risk_amount,
        ti.status,
        ti.manual_confirmed_at,
        ti.submitted_at,
        ti.notes,
        ti.created_at,
        ti.updated_at
    FROM trade_instructions ti
    JOIN instruments i ON i.id = ti.instrument_id
"""

POSITION_SELECT = """
    SELECT
        p.id,
        p.trade_instruction_id,
        p.screening_result_id,
        i.fyers_symbol AS symbol,
        i.symbol AS display_symbol,
        p.state,
        p.side,
        p.quantity,
        p.open_quantity,
        p.product_type,
        p.average_entry_price,
        p.current_stop_loss,
        p.current_target,
        p.trailing_rule,
        p.realized_pnl,
        p.opened_at,
        p.closed_at,
        p.created_at,
        p.updated_at
    FROM positions p
    JOIN instruments i ON i.id = p.instrument_id
"""

ORDER_INTENT_SELECT = """
    SELECT
        oi.id,
        oi.idempotency_key,
        oi.trade_instruction_id,
        oi.position_id,
        i.fyers_symbol AS symbol,
        i.symbol AS display_symbol,
        oi.intent_type,
        oi.side,
        oi.quantity,
        oi.product_type,
        oi.order_type,
        oi.limit_price,
        oi.trigger_price,
        oi.status,
        oi.execution_mode,
        oi.fyers_async_id,
        oi.fyers_order_id,
        oi.exchange_order_id,
        oi.broker_requested_at,
        oi.broker_responded_at,
        oi.requested_by_component,
        oi.reason,
        oi.created_at,
        oi.updated_at
    FROM order_intents oi
    LEFT JOIN trade_instructions ti ON ti.id = oi.trade_instruction_id
    LEFT JOIN positions p ON p.id = oi.position_id
    JOIN instruments i ON i.id = COALESCE(ti.instrument_id, p.instrument_id)
"""


async def create_trade_instruction(
    db: AsyncSession,
    payload: TradeInstructionCreate,
) -> TradeInstructionView:
    instrument_result = await db.execute(
        text(
            """
            SELECT id, lot_size, tick_size
            FROM instruments
            WHERE fyers_symbol = :symbol AND active = true
            """
        ),
        {"symbol": payload.symbol},
    )
    instrument = instrument_result.mappings().one_or_none()
    if instrument is None:
        raise TradeNotFoundError(
            f"Active instrument '{payload.symbol}' was not found."
        )

    if payload.screening_result_id is not None:
        screening_result = await db.execute(
            text(
                """
                SELECT 1
                FROM screening_results
                WHERE id = :screening_result_id
                  AND instrument_id = :instrument_id
                """
            ),
            {
                "screening_result_id": payload.screening_result_id,
                "instrument_id": instrument["id"],
            },
        )
        if screening_result.scalar_one_or_none() is None:
            raise TradeConflictError(
                "screening_result_id does not belong to the selected instrument."
            )

    validate_trade_plan(
        side=payload.side,
        quantity=payload.quantity,
        lot_size=instrument["lot_size"],
        tick_size=Decimal(instrument["tick_size"]),
        planned_entry_price=payload.planned_entry_price,
        entry_order_type=payload.entry_order_type,
        entry_limit_price=payload.entry_limit_price,
        initial_stop_loss=payload.initial_stop_loss,
        initial_target=payload.initial_target,
    )

    instruction_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO trade_instructions (
                id,
                instrument_id,
                screening_result_id,
                side,
                quantity,
                product_type,
                entry_order_type,
                planned_entry_price,
                entry_limit_price,
                initial_stop_loss,
                initial_target,
                trailing_rule,
                risk_amount,
                status,
                notes
            )
            VALUES (
                :id,
                :instrument_id,
                :screening_result_id,
                :side,
                :quantity,
                :product_type,
                :entry_order_type,
                :planned_entry_price,
                :entry_limit_price,
                :initial_stop_loss,
                :initial_target,
                CAST(:trailing_rule AS jsonb),
                :risk_amount,
                'draft',
                :notes
            )
            """
        ),
        {
            "id": instruction_id,
            "instrument_id": instrument["id"],
            "screening_result_id": payload.screening_result_id,
            "side": payload.side,
            "quantity": payload.quantity,
            "product_type": payload.product_type,
            "entry_order_type": payload.entry_order_type,
            "planned_entry_price": payload.planned_entry_price,
            "entry_limit_price": payload.entry_limit_price,
            "initial_stop_loss": payload.initial_stop_loss,
            "initial_target": payload.initial_target,
            "trailing_rule": json.dumps(
                {
                    "type": payload.trailing_rule.type,
                    "value": (
                        float(payload.trailing_rule.value)
                        if payload.trailing_rule.value is not None
                        else None
                    ),
                }
            ),
            "risk_amount": payload.risk_amount,
            "notes": payload.notes,
        },
    )
    return await get_trade_instruction(db, instruction_id)


async def get_trade_instruction(
    db: AsyncSession,
    instruction_id: UUID,
) -> TradeInstructionView:
    result = await db.execute(
        text(f"{TRADE_INSTRUCTION_SELECT} WHERE ti.id = :instruction_id"),
        {"instruction_id": instruction_id},
    )
    row = result.mappings().one_or_none()
    if row is None:
        raise TradeNotFoundError("Trade instruction was not found.")
    return TradeInstructionView.model_validate(dict(row))


async def list_trade_instructions(
    db: AsyncSession,
    *,
    limit: int,
) -> list[TradeInstructionView]:
    result = await db.execute(
        text(f"{TRADE_INSTRUCTION_SELECT} ORDER BY ti.created_at DESC LIMIT :limit"),
        {"limit": limit},
    )
    return [
        TradeInstructionView.model_validate(dict(row))
        for row in result.mappings().all()
    ]


async def list_positions(
    db: AsyncSession,
    *,
    active_only: bool,
    limit: int,
) -> list[PositionView]:
    where = (
        "WHERE p.state NOT IN ('closed', 'cancelled')"
        if active_only
        else ""
    )
    result = await db.execute(
        text(f"{POSITION_SELECT} {where} ORDER BY p.created_at DESC LIMIT :limit"),
        {"limit": limit},
    )
    return [PositionView.model_validate(dict(row)) for row in result.mappings().all()]


async def list_order_intents(
    db: AsyncSession,
    *,
    limit: int,
) -> list[OrderIntentView]:
    result = await db.execute(
        text(f"{ORDER_INTENT_SELECT} ORDER BY oi.created_at DESC LIMIT :limit"),
        {"limit": limit},
    )
    return [
        OrderIntentView.model_validate(dict(row))
        for row in result.mappings().all()
    ]


async def load_confirmation_records(
    db: AsyncSession,
    *,
    instruction_id: UUID,
) -> tuple[PositionView, OrderIntentView]:
    position_result = await db.execute(
        text(
            f"""
            {POSITION_SELECT}
            WHERE p.trade_instruction_id = :instruction_id
            """
        ),
        {"instruction_id": instruction_id},
    )
    position_row = position_result.mappings().one_or_none()

    intent_result = await db.execute(
        text(
            f"""
            {ORDER_INTENT_SELECT}
            WHERE oi.trade_instruction_id = :instruction_id
              AND oi.intent_type = 'entry'
            """
        ),
        {"instruction_id": instruction_id},
    )
    intent_row = intent_result.mappings().one_or_none()
    if position_row is None or intent_row is None:
        raise TradeConflictError(
            "Confirmed instruction has an incomplete position/intent audit trail."
        )
    return (
        PositionView.model_validate(dict(position_row)),
        OrderIntentView.model_validate(dict(intent_row)),
    )


async def confirm_trade_instruction(
    db: AsyncSession,
    instruction_id: UUID,
) -> TradeConfirmationResult:
    locked_result = await db.execute(
        text(
            """
            SELECT
                ti.*,
                i.lot_size,
                i.tick_size
            FROM trade_instructions ti
            JOIN instruments i ON i.id = ti.instrument_id
            WHERE ti.id = :instruction_id
            FOR UPDATE OF ti
            """
        ),
        {"instruction_id": instruction_id},
    )
    instruction = locked_result.mappings().one_or_none()
    if instruction is None:
        raise TradeNotFoundError("Trade instruction was not found.")

    if instruction["status"] in {"confirmed", "submitted"}:
        position, intent = await load_confirmation_records(
            db,
            instruction_id=instruction_id,
        )
        return TradeConfirmationResult(
            instruction=await get_trade_instruction(db, instruction_id),
            position=position,
            order_intent=intent,
            idempotent_replay=True,
            broker_call_made=False,
        )
    if instruction["status"] != "draft":
        raise TradeConflictError(
            f"Only a draft can be confirmed; current status is "
            f"'{instruction['status']}'."
        )

    validate_trade_plan(
        side=instruction["side"],
        quantity=instruction["quantity"],
        lot_size=instruction["lot_size"],
        tick_size=Decimal(instruction["tick_size"]),
        planned_entry_price=instruction["planned_entry_price"],
        entry_order_type=instruction["entry_order_type"],
        entry_limit_price=instruction["entry_limit_price"],
        initial_stop_loss=instruction["initial_stop_loss"],
        initial_target=instruction["initial_target"],
    )

    # Fail before changing the manual checkpoint if automation is paused.
    await ensure_orders_allowed(db)

    position_id = uuid4()
    await db.execute(
        text(
            """
            UPDATE trade_instructions
            SET status = 'confirmed', manual_confirmed_at = now()
            WHERE id = :instruction_id
            """
        ),
        {"instruction_id": instruction_id},
    )
    await db.execute(
        text(
            """
            INSERT INTO positions (
                id,
                instrument_id,
                trade_instruction_id,
                screening_result_id,
                state,
                side,
                quantity,
                open_quantity,
                product_type,
                current_stop_loss,
                current_target,
                trailing_rule
            )
            VALUES (
                :id,
                :instrument_id,
                :trade_instruction_id,
                :screening_result_id,
                'pending_entry',
                :side,
                :quantity,
                0,
                :product_type,
                :current_stop_loss,
                :current_target,
                CAST(:trailing_rule AS jsonb)
            )
            """
        ),
        {
            "id": position_id,
            "instrument_id": instruction["instrument_id"],
            "trade_instruction_id": instruction_id,
            "screening_result_id": instruction["screening_result_id"],
            "side": "long" if instruction["side"] == "buy" else "short",
            "quantity": instruction["quantity"],
            "product_type": instruction["product_type"],
            "current_stop_loss": instruction["initial_stop_loss"],
            "current_target": instruction["initial_target"],
            "trailing_rule": json.dumps(instruction["trailing_rule"]),
        },
    )
    await db.execute(
        text(
            """
            INSERT INTO position_events (
                position_id,
                event_type,
                to_state,
                trigger_source,
                stop_loss_price,
                target_price,
                trailing_rule,
                details
            )
            VALUES (
                :position_id,
                'entry_pending',
                'pending_entry',
                'api',
                :stop_loss_price,
                :target_price,
                CAST(:trailing_rule AS jsonb),
                CAST(:details AS jsonb)
            )
            """
        ),
        {
            "position_id": position_id,
            "stop_loss_price": instruction["initial_stop_loss"],
            "target_price": instruction["initial_target"],
            "trailing_rule": json.dumps(instruction["trailing_rule"]),
            "details": json.dumps(
                {
                    "manual_checkpoint": True,
                    "execution_mode": settings.execution_mode,
                    "broker_call_made": False,
                }
            ),
        },
    )

    if instruction["screening_result_id"] is not None:
        await db.execute(
            text(
                """
                UPDATE screening_results
                SET reviewer_status = 'trade_planned'
                WHERE id = :screening_result_id
                """
            ),
            {"screening_result_id": instruction["screening_result_id"]},
        )

    await create_entry_intent(
        db,
        trade_instruction_id=instruction_id,
        position_id=position_id,
        side=instruction["side"],
        quantity=instruction["quantity"],
        product_type=instruction["product_type"],
        order_type=instruction["entry_order_type"],
        limit_price=instruction["entry_limit_price"],
    )

    position, intent = await load_confirmation_records(
        db,
        instruction_id=instruction_id,
    )
    return TradeConfirmationResult(
        instruction=await get_trade_instruction(db, instruction_id),
        position=position,
        order_intent=intent,
        idempotent_replay=False,
        broker_call_made=False,
    )
